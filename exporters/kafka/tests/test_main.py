"""App-flow tests: topic mapping, consume -> buffer -> drain, via the SDK harness with a fake writer."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Boolean, Number, String
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from store import Records, Store

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
    main._topics = {}
    main.store = Store(db_path=str(tmp_path / "data.db"))


def _assets(mapping: dict) -> dict:
    """Fake `app.assets`: {asset: {stream: configuration_dict}}."""
    return {
        asset: SimpleNamespace(datastreams={
            stream: SimpleNamespace(configuration=cfg) for stream, cfg in streams.items()
        })
        for asset, streams in mapping.items()
    }


def _manifest():
    # add_input is required: the harness silently drops any datastream not declared here.
    # Each stream carries its Kafka topic in per-stream IO configuration (MQTT-importer style).
    return (
        ManifestBuilder.from_app_yaml()
        .add_asset("pump-1")
        .add_input("temperature", "number", configuration={"topic": "telemetry.{asset}"})
        .add_input("status", "string", configuration={"topic": "status"})
        .add_input("enabled", "boolean", configuration={"topic": "status"})
        .add_input("orphan", "number")                      # no topic: must never buffer
        .set_configuration({
            "kafka": {"bootstrap_servers": "b:9092"},
            "upload": {"batch_size": 100, "interval": 1},   # VirtualClock fast-forwards the interval (no real wait)
        })
        .build()
    )


class TestBuildTopicMap:
    """Pure helper: per-stream topic resolution with {asset}/{stream} placeholders."""

    def test_resolves_placeholders(self) -> None:
        tm = main.build_topic_map(_assets({"pump-1": {"temp": {"topic": "telemetry.{asset}.{stream}"}}}))
        assert tm == {("pump-1", "temp"): "telemetry.pump-1.temp"}

    def test_literal_topic_unchanged(self) -> None:
        tm = main.build_topic_map(_assets({"pump-1": {"temp": {"topic": "telemetry"}}}))
        assert tm == {("pump-1", "temp"): "telemetry"}

    def test_warns_and_skips_stream_without_topic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No topic and no default: the stream is warned once and left unmapped."""
        warnings: list[dict] = []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append(kw))
        assert main.build_topic_map(_assets({"pump-1": {"orphan": {}}})) == {}
        assert warnings == [{"asset": "pump-1", "stream": "orphan"}]


class TestBuildPriorityMap:
    """Pure helper: per-stream priority extraction from IO configuration."""

    def test_collects_explicit_priorities(self) -> None:
        pm = main.build_priority_map(_assets({"pump-1": {
            "critical": {"topic": "t", "priority": 1},
            "normal": {"topic": "t", "priority": 2},
        }}))
        assert pm == {("pump-1", "critical"): 1, ("pump-1", "normal"): 2}

    def test_omits_streams_without_a_priority(self) -> None:
        """Unset priority stays out of the map: the store ranks those Normal by default."""
        assert main.build_priority_map(_assets({"pump-1": {"temp": {"topic": "t"}}})) == {}

    def test_coerces_numeric_strings(self) -> None:
        """A priority that arrives as a JSON string still ranks as its integer value."""
        pm = main.build_priority_map(_assets({"pump-1": {"temp": {"topic": "t", "priority": "1"}}}))
        assert pm == {("pump-1", "temp"): 1}


@pytest.mark.asyncio
async def test_handlers_registered() -> None:
    """Both the stream consumer and the drain task are registered on the app."""
    assert {"on_data", "export"} <= {k.rsplit(".", 1)[-1] for k in main.app.tasks}


@pytest.mark.asyncio
async def test_three_types_buffer_and_drain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """number/string/boolean messages on mapped streams are buffered and drained with native types."""
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "KafkaWriter", lambda cfg, topics: fake)   # main builds KafkaWriter(kafka, topics)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish_batch([
            Number(resource=KRNAssetDataStream("pump-1", "temperature"), payload=42.5),
            String(resource=KRNAssetDataStream("pump-1", "status"), payload="running"),
            Boolean(resource=KRNAssetDataStream("pump-1", "enabled"), payload=True),
        ])
        # run_until_idle consumes inputs (Phase 2) only after advancing the clock (Phase 1),
        # so the first pass buffers the messages and the second pass lets the drain tick read
        # them. Two passes is the deterministic pattern for a time-driven drain loop.
        await harness.run_until_idle(timeout=5.0)   # consume inputs -> buffer
        await harness.run_until_idle(timeout=5.0)   # advance clock -> drain reads the batch

    assert fake.batches, "drain task never ran"
    sent = {r["datastream"]: r["payload"] for b in fake.batches for r in b}
    assert sent["temperature"] == 42.5
    assert sent["status"] == "running"
    assert sent["enabled"] is True


@pytest.mark.asyncio
async def test_topic_map_built_from_io_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """on_connect resolves each mapped stream's topic ({asset} templated) and skips the orphan."""
    _reset(tmp_path)
    monkeypatch.setattr(main, "KafkaWriter", lambda cfg, topics: FakeWriter())

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.run_until_idle(timeout=5.0)
        assert main._topics == {
            ("pump-1", "temperature"): "telemetry.pump-1",
            ("pump-1", "status"): "status",
            ("pump-1", "enabled"): "status",
        }


@pytest.mark.asyncio
async def test_high_priority_stream_drains_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With batch_size=1, the High-priority stream's row fills the first batch even though the
    Normal stream's row was buffered before it (selection order comes from the IO config)."""
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "KafkaWriter", lambda cfg, topics: fake)
    manifest = (
        ManifestBuilder.from_app_yaml()
        .add_asset("pump-1")
        .add_input("background", "number", configuration={"topic": "t"})
        .add_input("critical", "number", configuration={"topic": "t", "priority": 1})
        .set_configuration({
            "kafka": {"bootstrap_servers": "b:9092"},
            "upload": {"batch_size": 1, "interval": 1},
        })
        .build()
    )

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        await harness.publish_batch([
            Number(resource=KRNAssetDataStream("pump-1", "background"), payload=1.0),  # buffered first
            Number(resource=KRNAssetDataStream("pump-1", "critical"), payload=2.0),
        ])
        await harness.run_until_idle(timeout=5.0)   # consume inputs -> buffer
        await harness.run_until_idle(timeout=5.0)   # advance clock -> drain tick(s)

    assert fake.batches, "drain task never ran"
    assert fake.batches[0][0]["datastream"] == "critical", "High-priority row must drain first"


@pytest.mark.asyncio
async def test_unmapped_stream_is_never_buffered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Data on a stream without a topic is dropped at append: it can't pile up with no destination."""
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "KafkaWriter", lambda cfg, topics: fake)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish_batch([
            Number(resource=KRNAssetDataStream("pump-1", "orphan"), payload=9.9),      # unmapped
            Number(resource=KRNAssetDataStream("pump-1", "temperature"), payload=1.0),  # mapped
        ])
        await harness.run_until_idle(timeout=5.0)
        await harness.run_until_idle(timeout=5.0)

    drained = {r["datastream"] for b in fake.batches for r in b}
    assert drained == {"temperature"}, "orphan stream must not reach the buffer"


@pytest.mark.asyncio
async def test_on_disconnect_tears_down_writer_and_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Leaving the app context fires on_disconnect, which tears down the writer and closes the buffer."""
    _reset(tmp_path)
    fake = FakeWriter()
    monkeypatch.setattr(main, "KafkaWriter", lambda cfg, topics: fake)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.run_until_idle(timeout=5.0)

    assert fake.torn_down, "on_disconnect did not tear down the writer"
    assert main.store._con is None, "on_disconnect did not close the buffer connection"


@pytest.mark.asyncio
async def test_invalid_configuration_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A SASL_* protocol without a sasl block fails on_connect loudly instead of starting half-built."""
    _reset(tmp_path)
    bad = (
        ManifestBuilder.from_app_yaml()
        .add_asset("pump-1")
        .add_input("temperature", "number", configuration={"topic": "t"})
        .set_configuration({
            "kafka": {"bootstrap_servers": "b:9092",
                      "security": {"protocol": "SASL_SSL"}},   # no sasl block -> coherence check fails
        })
        .build()
    )
    with pytest.raises(Exception):
        async with KelvinAppTest(main.app, manifest=bad) as harness:
            await harness.run_until_idle(timeout=5.0)
