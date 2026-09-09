"""Real-broker integration tests (a live Kafka via testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. Unlike the unit/harness tests,
these drive the connector's OWN shipped entrypoint, `main._consume(settings)`, against a real broker:
one consumer/producer pair, the real read loop, the real command queue + command loop, and the real
Message publish path. Every case produces real bytes to real topics and asserts on what actually lands,
so the wire boundary the fakes paper over (decode, offsets, keys, templated topics) is exercised for real.

Each test uses unique topics + group_id so they can't cross-talk, reuses one module-scoped broker, and
tears the background _consume task and its clients down in a finally.
"""
import asyncio
import contextlib
import uuid
from types import SimpleNamespace

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from kelvin.krn import KRNAssetDataStream
from kelvin.message import ControlChangeStatus
from kelvin.message.base_messages import StateEnum
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def bootstrap():
    """Boot one Kafka broker for the module (image pull happens on first run)."""
    from testcontainers.kafka import KafkaContainer

    with KafkaContainer() as kafka:
        yield kafka.get_bootstrap_server()


@pytest.fixture
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own _commands queue and _started event.

    The module-level pair binds to the first event loop that touches it; a fresh pair per test keeps
    _consume / _command_loop / on_control_change from crossing loops between tests (each async test
    runs on its own loop)."""
    monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=main._MAX_QUEUED_COMMANDS))
    monkeypatch.setattr(main, "_started", asyncio.Event())


# --- helpers --------------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _settings(bootstrap: str, group: str, reset: str = "earliest") -> Settings:
    return Settings(kafka={"bootstrap_servers": bootstrap, "group_id": group, "auto_offset_reset": reset})


def _manifest(bootstrap: str, streams: list[tuple[str, str, dict]], *,
              group: str = "itest", reset: str = "earliest", assets: tuple[str, ...] = ("pump-1",)):
    """Build a runtime manifest with the given per-stream IO configuration.

    _consume reads its topics/keys from app.assets (the manifest), so this drives real routing.
    The kafka config here is inert for these tests: _consume takes its Settings as an argument."""
    mb = ManifestBuilder.from_app_yaml()
    for asset in assets:
        mb = mb.add_asset(asset)
    for name, dtype, cfg in streams:
        mb = mb.add_input(name, dtype, configuration=cfg)
    return mb.set_configuration(
        {"kafka": {"bootstrap_servers": bootstrap, "group_id": group, "auto_offset_reset": reset}}
    ).build()


async def _produce(bootstrap: str, records: list[tuple[str, bytes, bytes | None]]) -> None:
    """Send (topic, value, key) records with a short-lived producer (auto-creates the topics)."""
    kw = await main._client_kwargs(_settings(bootstrap, "producer").kafka)
    producer = AIOKafkaProducer(**kw)
    await producer.start()
    try:
        for topic, value, key in records:
            await producer.send_and_wait(topic, value=value, key=key)
    finally:
        await producer.stop()


async def _read_back(bootstrap: str, topic: str, timeout: float = 30.0):
    """Consume one record from `topic` with a throwaway group, from the earliest offset."""
    kw = await main._client_kwargs(_settings(bootstrap, f"verify-{_uid()}").kafka)
    consumer = AIOKafkaConsumer(topic, group_id=f"verify-{_uid()}", auto_offset_reset="earliest", **kw)
    await consumer.start()
    try:
        return await asyncio.wait_for(consumer.getone(), timeout=timeout)
    finally:
        await consumer.stop()


async def _poll_until(predicate, *, tries: int = 600, delay: float = 0.05) -> None:
    """Poll `predicate` up to `tries` times (default ~30s), sleeping `delay` between checks.

    The background loops would run forever, so watch a condition (records landed) instead of a fixed
    wall-clock wait; a broken pipeline still fails fast once the surrounding assertion runs."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(delay)


async def _cancel(task: "asyncio.Task") -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _await_started(task: "asyncio.Task", timeout: float = 30.0) -> None:
    """Wait until _consume signals readiness (_started) OR its task finishes first.

    Racing the started-event against the task keeps a regression that raises BEFORE `_started.set()`
    from hanging forever (there is no pytest-timeout in this repo). If the task finished, re-raise its
    real exception instead of masking it behind a generic timeout."""
    started = asyncio.ensure_future(main._started.wait())
    done, _ = await asyncio.wait({started, task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    if not started.done():
        started.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await started
    if task in done:
        task.result()  # re-raise the real error _consume raised
    if not done:
        raise TimeoutError("timed out waiting for _consume readiness")


@contextlib.asynccontextmanager
async def _running_consume(settings: Settings):
    """Run main._consume as a background task, wait for its readiness signal, then tear it down.

    Clears _started BEFORE awaiting so a prior run's still-set event can't be mistaken for this run's
    readiness (matters for the restart test, which runs _consume twice on one event/queue pair)."""
    main._started.clear()
    task = asyncio.create_task(main._consume(settings))
    try:
        await _await_started(task)
        yield task
    finally:
        await _cancel(task)


# --- tests ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consume_loop_both_directions(bootstrap: str, fresh_state: None) -> None:
    """Full _consume path, both directions. A produced record flows through the real read loop into
    Kelvin; a control change enqueued via on_control_change is served by the real command loop, produced
    to its control_topic (keyed by asset), and acked `processed`."""
    ingest, control, group = f"telemetry-{_uid()}", f"cmd-{_uid()}", f"g-{_uid()}"
    manifest = _manifest(bootstrap, [
        ("temperature", "number", {"topic": ingest}),
        ("setpoint", "number", {"control_topic": control}),
    ], group=group)

    await _produce(bootstrap, [(ingest, b"42.5", None)])  # earliest + fresh group => read on startup

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        async with _running_consume(_settings(bootstrap, group)):
            # inbound: record -> Kelvin
            await _poll_until(lambda: any(
                o.resource.data_stream == "temperature" and o.payload == 42.5 for o in harness.outputs))
            assert any(o.resource.data_stream == "temperature" and o.payload == 42.5
                       for o in harness.outputs), "inbound record never reached Kelvin"

            # outbound: control change -> control_topic + processed ack
            cc = SimpleNamespace(resource=KRNAssetDataStream("pump-1", "setpoint"),
                                 id=uuid.uuid4(), payload=SimpleNamespace(payload=55))
            await main.on_control_change(cc)

            rec = await _read_back(bootstrap, control)
            assert rec.value == b"55" and rec.key == b"pump-1"

            await _poll_until(lambda: any(
                isinstance(o, ControlChangeStatus) and o.payload.control_change_id == cc.id
                for o in harness.outputs))
            acks = [o for o in harness.outputs
                    if isinstance(o, ControlChangeStatus) and o.payload.control_change_id == cc.id]

    assert acks and acks[-1].payload.state == StateEnum.processed


@pytest.mark.asyncio
async def test_restart_resumes_from_committed_offset(bootstrap: str, fresh_state: None) -> None:
    """Auto-commit + a clean stop commits the consumed offset; a restart with the SAME group reads only
    the records produced after the stop. The committed offset wins over `earliest`, so record A (already
    consumed) is not redelivered and only record B arrives on the second run. This is the one behavior a
    fake consumer can't reproduce."""
    topic, group = f"telemetry-{_uid()}", f"g-{_uid()}"
    manifest = _manifest(bootstrap, [("temperature", "number", {"topic": topic})], group=group)
    settings = _settings(bootstrap, group)

    await _produce(bootstrap, [(topic, b"1.0", None)])  # record A, before the first run

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        async with _running_consume(settings):
            await _poll_until(lambda: any(o.payload == 1.0 for o in harness.outputs))
        # first run stopped cleanly here -> aiokafka committed the offset past A
        assert [o.payload for o in harness.outputs] == [1.0]
        seen = len(harness.outputs)

        await _produce(bootstrap, [(topic, b"2.0", None)])  # record B, only after A was committed

        async with _running_consume(settings):
            await _poll_until(lambda: len(harness.outputs) > seen)
        delivered = [o.payload for o in harness.outputs[seen:]]

    assert delivered == [2.0], f"restart should deliver only B (A was committed); got {delivered}"


@pytest.mark.asyncio
async def test_multi_type_payloads_over_the_wire(bootstrap: str, fresh_state: None) -> None:
    """number / string / boolean / object, plus one payload_field JSON extraction, produced as real
    bytes to distinct topics; each publishes to Kelvin with its native Python type."""
    s = _uid()
    t_num, t_str, t_bool, t_obj, t_field = (f"{p}-{s}" for p in ("num", "str", "bool", "obj", "field"))
    group = f"g-{_uid()}"
    manifest = _manifest(bootstrap, [
        ("temperature", "number", {"topic": t_num}),
        ("status", "string", {"topic": t_str}),
        ("enabled", "boolean", {"topic": t_bool}),
        ("readings", "object", {"topic": t_obj}),
        ("pressure", "number", {"topic": t_field, "payload_field": "readings.pressure"}),
    ], group=group)

    await _produce(bootstrap, [
        (t_num, b"42.5", None),
        (t_str, b"running", None),
        (t_bool, b"true", None),
        (t_obj, b'{"p": 1}', None),
        (t_field, b'{"readings": {"pressure": 4.2}}', None),
    ])

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        async with _running_consume(_settings(bootstrap, group)):
            await _poll_until(lambda: len({o.resource.data_stream for o in harness.outputs}) >= 5)
        out = {o.resource.data_stream: o.payload for o in harness.outputs}

    assert out["temperature"] == 42.5 and isinstance(out["temperature"], float)
    assert out["status"] == "running" and isinstance(out["status"], str)
    assert out["enabled"] is True                              # native bool, not "true"
    assert out["readings"] == {"p": 1}                         # native object
    assert out["pressure"] == 4.2                              # extracted from readings.pressure


@pytest.mark.asyncio
async def test_templated_topic_and_key_filter_routing(bootstrap: str, fresh_state: None) -> None:
    """A {asset}.{stream} templated subscription resolves against the broker, and on a shared topic a
    key-filtered mapping receives only its key while an unkeyed mapping receives every record."""
    shared, group = f"shared-{_uid()}", f"g-{_uid()}"
    manifest = _manifest(bootstrap, [
        ("reading", "number", {"topic": "{asset}.{stream}"}),   # resolves to pump-1.reading
        ("keyed", "number", {"topic": shared, "key": "{asset}"}),  # only key == pump-1
        ("everything", "number", {"topic": shared}),            # any key
    ], group=group)
    resolved = "pump-1.reading"

    await _produce(bootstrap, [
        (resolved, b"9.0", None),
        (shared, b"1.0", b"pump-1"),
        (shared, b"2.0", b"pump-2"),
    ])

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        async with _running_consume(_settings(bootstrap, group)):
            await _poll_until(lambda: len(harness.outputs) >= 4)  # reading:1 + keyed:1 + everything:2
        by_stream: dict[str, list] = {}
        for o in harness.outputs:
            by_stream.setdefault(o.resource.data_stream, []).append(o.payload)

    assert by_stream["reading"] == [9.0]                        # templated topic resolved on the broker
    assert by_stream["keyed"] == [1.0]                          # only the matching key survives the filter
    assert sorted(by_stream["everything"]) == [1.0, 2.0]        # unkeyed mapping sees every record


@pytest.mark.asyncio
async def test_malformed_record_skipped_sibling_delivers(bootstrap: str, fresh_state: None) -> None:
    """Garbage on a number stream is skipped; the valid record that follows still publishes and the
    live consumer loop keeps running (a bad record must never kill the pipeline)."""
    topic, group = f"telemetry-{_uid()}", f"g-{_uid()}"
    manifest = _manifest(bootstrap, [("temperature", "number", {"topic": topic})], group=group)

    await _produce(bootstrap, [(topic, b"not-a-number", None), (topic, b"42.5", None)])

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        async with _running_consume(_settings(bootstrap, group)) as task:
            await _poll_until(lambda: any(o.payload == 42.5 for o in harness.outputs))
            assert any(o.payload == 42.5 for o in harness.outputs), "valid record never published"
            assert not task.done(), "read loop died on the malformed record"
        out = [o.payload for o in harness.outputs]

    assert out == [42.5]  # garbage skipped, only the valid sibling published
