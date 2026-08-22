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
    main._settings = None
    main._writer = None
    main.store = Store(db_path=str(tmp_path / "data.db"))


def _manifest():
    return (
        ManifestBuilder.from_app_yaml()
        .add_asset("pump-1")
        .add_input("temperature", "number")
        .add_input("status", "string")
        .add_input("enabled", "boolean")
        .set_configuration({
            "sftp": {"host": "sftp.example.com", "username": "svc",
                     "auth": {"method": "password", "password": "pw"}},
            "upload": {"batch_size": 100, "interval": 1},
        })
        .build()
    )


async def test_handlers_registered() -> None:
    assert {"on_data", "export"} <= {k.rsplit(".", 1)[-1] for k in main.app.tasks}


async def test_three_types_buffer_and_drain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """number/string/boolean messages are buffered and drained with native types intact."""
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "SftpWriter", lambda cfg, fmt: fake)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish_batch([
            Number(resource=KRNAssetDataStream("pump-1", "temperature"), payload=42.5),
            String(resource=KRNAssetDataStream("pump-1", "status"), payload="running"),
            Boolean(resource=KRNAssetDataStream("pump-1", "enabled"), payload=True),
        ])
        await harness.run_until_idle(timeout=5.0)
        await harness.run_until_idle(timeout=5.0)

    assert fake.batches, "drain task never ran"
    sent = {r["datastream"]: r["payload"] for b in fake.batches for r in b}
    assert sent["temperature"] == 42.5 and sent["status"] == "running" and sent["enabled"] is True


async def test_on_disconnect_tears_down_writer_and_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "SftpWriter", lambda cfg, fmt: fake)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.run_until_idle(timeout=5.0)

    assert fake.torn_down and main.store._con is None


async def test_invalid_configuration_raises(tmp_path: Path) -> None:
    """A config missing the auth credential fails on_connect loudly."""
    _reset(tmp_path)
    bad = (
        ManifestBuilder.from_app_yaml()
        .add_asset("pump-1")
        .add_input("temperature", "number")
        .set_configuration({
            "sftp": {"host": "sftp.example.com", "username": "svc", "auth": {"method": "password"}},
        })
        .build()
    )
    with pytest.raises(Exception):
        async with KelvinAppTest(main.app, manifest=bad) as harness:
            await harness.run_until_idle(timeout=5.0)
