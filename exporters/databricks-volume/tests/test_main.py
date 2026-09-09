"""App-flow tests: consume -> buffer -> drain, via the SDK harness with a fake writer."""
from pathlib import Path

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Boolean, Number, String
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from store import Records, Store

pytestmark = pytest.mark.asyncio


class FakeWriter:
    """Implements the Writer protocol; records what it drained and whether it was torn down."""

    fmt = None

    def __init__(self, *_: object) -> None:
        self.batches: list[list[dict]] = []
        self.torn_down = False

    async def setup(self) -> None: ...

    async def write_batch(self, store: Store, limit: int) -> Records:
        result = await store.read(limit)
        if result.n_rows:
            self.batches.append(result.rows)
        return result

    async def teardown(self) -> None:
        self.torn_down = True


def _reset(tmp_path: Path) -> None:
    """Module-level state persists across tests; reset it to a fresh buffer."""
    main._settings = None
    main._writer = None
    main.store = Store(db_path=str(tmp_path / "data.db"))


def _manifest():
    # add_input is required: the harness silently drops any datastream not declared here.
    return (
        ManifestBuilder.from_app_yaml()
        .add_asset("pump-1")
        .add_input("temperature", "number")
        .add_input("status", "string")
        .add_input("enabled", "boolean")
        .set_configuration({
            "databricks": {
                "server_hostname": "dbc-123.cloud.databricks.com",
                "delta_table": "main.telemetry.readings",
                "uc_volume": "main.telemetry.landing",
                "auth": {"method": "access_token", "access_token": "tok"},
            },
            "upload": {"batch_size": 100, "interval": 1},   # VirtualClock fast-forwards the interval
        })
        .build()
    )


async def test_handlers_registered() -> None:
    """Both the stream consumer and the drain task are registered on the app."""
    assert {"on_data", "export"} <= {k.rsplit(".", 1)[-1] for k in main.app.tasks}


async def test_three_types_buffer_and_drain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """number/string/boolean messages are buffered and drained with native types intact."""
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "VolumeWriter", lambda cfg, fmt: fake)   # main builds VolumeWriter(databricks, upload.format)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish_batch([
            Number(resource=KRNAssetDataStream("pump-1", "temperature"), payload=42.5),
            String(resource=KRNAssetDataStream("pump-1", "status"), payload="running"),
            Boolean(resource=KRNAssetDataStream("pump-1", "enabled"), payload=True),
        ])
        await harness.run_until_idle(timeout=5.0)   # consume inputs -> buffer
        await harness.run_until_idle(timeout=5.0)   # advance clock -> drain reads the batch

    assert fake.batches, "drain task never ran"
    sent = {r["datastream"]: r["payload"] for b in fake.batches for r in b}
    assert sent["temperature"] == 42.5
    assert sent["status"] == "running"
    assert sent["enabled"] is True


async def test_on_disconnect_tears_down_writer_and_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Leaving the app context fires on_disconnect, which tears down the writer and closes the buffer."""
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "VolumeWriter", lambda cfg, fmt: fake)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.run_until_idle(timeout=5.0)

    assert fake.torn_down, "on_disconnect did not tear down the writer"
    assert main.store._con is None, "on_disconnect did not close the buffer connection"


async def test_invalid_configuration_raises(tmp_path: Path) -> None:
    """A config missing required databricks fields fails on_connect loudly instead of starting half-built."""
    _reset(tmp_path)
    bad = (
        ManifestBuilder.from_app_yaml()
        .add_asset("pump-1")
        .add_input("temperature", "number")
        .set_configuration({
            "databricks": {
                "server_hostname": "dbc-123.cloud.databricks.com",
                "delta_table": "main.telemetry.readings",
                "uc_volume": "main.telemetry.landing",
                "auth": {"method": "oauth", "client_id": "cid"},   # missing client_secret -> validator fails
            },
        })
        .build()
    )
    with pytest.raises(Exception):
        async with KelvinAppTest(main.app, manifest=bad) as harness:
            await harness.run_until_idle(timeout=5.0)
