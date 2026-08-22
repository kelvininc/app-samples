"""Real-broker integration tests (a live Kafka broker via testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. These drive the real
`KafkaWriter` (aiokafka over the wire) and the real `Store` against one Kafka container, then
consume the topics back; the actual produce path the unit tests fake with a `_FakeProducer`.

The whole module shares a single PLAINTEXT broker (booted once via the module-scoped
`bootstrap_servers` fixture) and each test uses its own topic names so tests never see each
other's records. Every test builds its own in-memory `Store`.

Scope note: SASL/TLS wiring (`client_kwargs`, `build_ssl_context`) can't be exercised here; the
plain `KafkaContainer` speaks PLAINTEXT with no broker-side auth, so those paths stay unit-tested.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import pytest
from aiokafka import AIOKafkaConsumer

import main
import writer as writer_mod
from drain import _tick
from settings import Buffer, Settings, Upload
from store import Store
from writer import KafkaWriter

pytestmark = pytest.mark.integration

# Store keeps timestamps as naive UTC; the exported string must carry an explicit "+00:00" marker.
_TS = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def bootstrap_servers():
    """Boot one Kafka broker for the whole module; yields its bootstrap servers string."""
    from testcontainers.kafka import KafkaContainer

    with KafkaContainer() as c:
        yield c.get_bootstrap_server()


def _kafka_cfg(servers: str):
    """A PLAINTEXT Kafka config block pointed at the container."""
    return Settings(kafka={"bootstrap_servers": servers,
                           "security": {"protocol": "PLAINTEXT"}}).kafka


async def _store_with(rows: list[tuple[str, str, object]]) -> Store:
    """A fresh in-memory Store seeded with (asset, datastream, payload) rows at `_TS`."""
    store = Store(":memory:")
    await store.setup()
    for asset, datastream, payload in rows:
        await store.append(_TS, asset, datastream, payload)
    return store


async def _consume(servers: str, topic: str, expected: int,
                   timeout_ms: int = 10000) -> list[tuple[Optional[str], dict]]:
    """Read up to `expected` messages from `topic`, returning (key, decoded-JSON-value) pairs.

    Polls until `expected` records arrive or the deadline passes, so a single fetch that returns
    fewer than everything available doesn't truncate the result. A unique group_id per topic keeps
    each read starting from the earliest offset (no committed-offset carry-over between tests).
    """
    consumer = AIOKafkaConsumer(topic, bootstrap_servers=servers,
                                auto_offset_reset="earliest", enable_auto_commit=False,
                                group_id=f"kelvin-kafka-exporter-it-{topic}")
    await consumer.start()
    out: list[tuple[Optional[str], dict]] = []
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000
        while len(out) < expected and loop.time() < deadline:
            remaining_ms = int((deadline - loop.time()) * 1000)
            batches = await consumer.getmany(timeout_ms=min(remaining_ms, 2000),
                                             max_records=expected - len(out))
            for msgs in batches.values():
                for m in msgs:
                    out.append((m.key.decode() if m.key else None, json.loads(m.value)))
    finally:
        await consumer.stop()
    return out


@pytest.mark.asyncio
async def test_multi_type_payloads_round_trip(bootstrap_servers) -> None:
    """number, string, AND boolean payloads all produce and consume back with their native JSON
    values, each keyed "asset/datastream", each carrying a UTC-marked ("+00:00") timestamp."""
    topic = "it-multitype"
    topics = {("pump-1", "temperature"): topic, ("pump-1", "label"): topic,
              ("pump-1", "active"): topic}
    store = await _store_with([("pump-1", "temperature", 42.5),
                               ("pump-1", "label", "running"),
                               ("pump-1", "active", True)])
    writer = KafkaWriter(_kafka_cfg(bootstrap_servers), topics)
    try:
        await writer.setup()
        result = await writer.write_batch(store, limit=1000)
    finally:
        await writer.teardown()
        await store.teardown()

    assert result.cursor is not None and result.n_rows == 3

    records = await _consume(bootstrap_servers, topic, expected=3)
    by_stream = {value["datastream"]: (key, value) for key, value in records}
    assert by_stream.keys() == {"temperature", "label", "active"}

    # native JSON values survive the round-trip: float stays float, str stays str, bool stays bool
    assert by_stream["temperature"][1]["payload"] == 42.5
    assert by_stream["label"][1]["payload"] == "running"
    assert by_stream["active"][1]["payload"] is True

    for stream, (key, value) in by_stream.items():
        assert value["asset"] == "pump-1"
        assert key == f"pump-1/{stream}"                    # key pins the stream to one partition
        assert value["timestamp"].endswith("+00:00")        # explicit UTC marker, never bare wall-clock
        assert datetime.fromisoformat(value["timestamp"]) == _TS


@pytest.mark.asyncio
async def test_topic_templating_routes_to_resolved_topics(bootstrap_servers) -> None:
    """A `{asset}`/`{stream}` template resolves per stream (via main.resolve) to distinct topics,
    and each record lands on its own resolved topic; consuming each topic proves the routing."""
    template = "it-route.{asset}.{stream}"
    streams = [("pump-1", "temperature", 21.0), ("pump-2", "pressure", 3.5)]
    topics = {(asset, stream): main.resolve(template, asset, stream)
              for asset, stream, _ in streams}
    temp_topic = topics[("pump-1", "temperature")]
    pressure_topic = topics[("pump-2", "pressure")]
    assert temp_topic != pressure_topic                     # templating fans out to different topics

    store = await _store_with([(a, s, p) for a, s, p in streams])
    writer = KafkaWriter(_kafka_cfg(bootstrap_servers), topics)
    try:
        await writer.setup()
        result = await writer.write_batch(store, limit=1000)
    finally:
        await writer.teardown()
        await store.teardown()

    assert result.n_rows == 2

    temp_records = await _consume(bootstrap_servers, temp_topic, expected=1)
    pressure_records = await _consume(bootstrap_servers, pressure_topic, expected=1)

    assert len(temp_records) == 1 and len(pressure_records) == 1
    (temp_key, temp_value), = temp_records
    (pressure_key, pressure_value), = pressure_records
    assert temp_key == "pump-1/temperature" and temp_value["asset"] == "pump-1"
    assert temp_value["datastream"] == "temperature" and temp_value["payload"] == 21.0
    assert pressure_key == "pump-2/pressure" and pressure_value["asset"] == "pump-2"
    assert pressure_value["datastream"] == "pressure" and pressure_value["payload"] == 3.5


@pytest.mark.asyncio
async def test_buffer_trimmed_only_after_ack(bootstrap_servers) -> None:
    """At-least-once: write_batch produces and awaits the broker acks but does NOT trim the buffer;
    the rows are dropped only by store.drop(cursor) once delivery is confirmed."""
    topic = "it-trim"
    topics = {("pump-1", "temperature"): topic, ("pump-1", "pressure"): topic}
    store = await _store_with([("pump-1", "temperature", 10.0), ("pump-1", "pressure", 20.0)])
    writer = KafkaWriter(_kafka_cfg(bootstrap_servers), topics)
    try:
        await writer.setup()
        result = await writer.write_batch(store, limit=1000)

        # write_batch awaits every delivery future (acks=all) before returning, so by now the
        # broker holds both rows -- yet the buffer is untouched: producing never trims.
        assert result.cursor is not None and result.n_rows == 2
        assert await store.count() == 2
        assert len(await _consume(bootstrap_servers, topic, expected=2)) == 2

        # only the explicit drop (what drain runs after a successful send) advances the buffer
        await store.drop(result.cursor)
        assert await store.count() == 0
        after = await store.read(limit=1000)
        assert after.cursor is None and after.n_rows == 0 and after.backlog == 0
    finally:
        await writer.teardown()
        await store.teardown()


@pytest.mark.asyncio
async def test_drain_tick_drops_after_successful_send(bootstrap_servers) -> None:
    """The real drain tick wires produce -> drop together: one _tick delivers the batch and then
    trims it, leaving the buffer empty (drop-after-ack end to end, no manual drop)."""
    topic = "it-drain"
    topics = {("pump-1", "temperature"): topic}
    store = await _store_with([("pump-1", "temperature", 55.5)])
    writer = KafkaWriter(_kafka_cfg(bootstrap_servers), topics)
    try:
        await writer.setup()
        # batch_size > backlog so _tick sleeps once at the end; a no-op clock skips real waiting.
        r = await _tick(writer, store, Upload(interval=1, batch_size=1000), Buffer(), _NoopClock())
        assert r is not None and r.n_rows == 1
        assert await store.count() == 0                     # tick dropped the delivered row
    finally:
        await writer.teardown()
        await store.teardown()

    (key, value), = await _consume(bootstrap_servers, topic, expected=1)
    assert key == "pump-1/temperature" and value["payload"] == 55.5


@pytest.mark.asyncio
async def test_poison_record_dead_lettered_sibling_delivers(
    bootstrap_servers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record too large for the producer's max_request_size is dead-lettered (dropped + warned)
    while the sibling in the same batch still delivers, and the batch's cursor still advances so
    the buffer can move past the poison. Exercises the poison path on the live broker."""
    topic = "it-poison"
    topics = {("pump-1", "temperature"): topic, ("pump-1", "blob"): topic}

    # Shrink max_request_size so an oversized payload trips aiokafka's real send-time size check
    # (_serialize -> MessageSizeTooLargeError), the same error write_batch dead-letters per record.
    real_producer = writer_mod.AIOKafkaProducer

    def small_producer(**kwargs):
        kwargs["max_request_size"] = 2048
        return real_producer(**kwargs)

    monkeypatch.setattr(writer_mod, "AIOKafkaProducer", small_producer)

    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(writer_mod.logger, "warning",
                        lambda msg, **kw: warnings.append((msg, kw)))

    store = await _store_with([("pump-1", "temperature", 7.0),
                               ("pump-1", "blob", "x" * 5000)])     # ~5 KB > 2 KB limit -> poison
    writer = KafkaWriter(_kafka_cfg(bootstrap_servers), topics)
    try:
        await writer.setup()
        result = await writer.write_batch(store, limit=1000)
    finally:
        await writer.teardown()
        await store.teardown()

    # the batch returned normally with the cursor over BOTH rows: the drain drops the poison too,
    # so the buffer advances past it instead of wedging on the un-producible record.
    assert result.cursor is not None and result.n_rows == 2
    assert writer._producer is None                         # torn down; not closed by the poison

    # only the good sibling reached the broker
    records = await _consume(bootstrap_servers, topic, expected=2, timeout_ms=6000)
    assert len(records) == 1
    (key, value), = records
    assert key == "pump-1/temperature" and value["datastream"] == "temperature"
    assert value["payload"] == 7.0

    # the poison was dead-lettered with a warning naming the offending record
    poison = [kw for msg, kw in warnings if "dead-letter" in msg.lower()]
    assert poison and poison[0]["datastream"] == "blob"
    assert poison[0]["error_type"] == "MessageSizeTooLargeError"


class _NoopClock:
    """Minimal ClockInterface stand-in: sleeps return immediately so a real drain tick doesn't
    wait out its interval. Only sleep()/now() are touched by _tick and _attempt."""

    async def sleep(self, seconds: float) -> None:
        return

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
