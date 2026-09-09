"""Real-broker smoke tests (a live Mosquitto via testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. These drive the connector's
own shipped code paths against a real broker, covering the wire boundary that the unit/harness
tests fake:

- `_handle_command` alone (one control-change writeback round-trip);
- the full `_consume` loop, which opens a real client, subscribes with the configured QoS, and
  runs the read/command/report loops together, so both directions travel over one shared client
  and the broker does the subscribe/wildcard matching instead of the client-side `topic.matches`.

MQTT has no durability for live messages, so each test either subscribes before anything is
published or (for `_consume`) waits for the connector's own "Connected to MQTT" log before
publishing. Retained messages are the exception and are exercised on purpose in one test.

Topics are namespaced per test (`_ns()`) so the module-shared broker can't leak state between
tests.
"""
import asyncio
import contextlib
import uuid
from types import SimpleNamespace

import aiomqtt
import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import ControlChangeStatus
from kelvin.message.base_messages import StateEnum
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main

pytestmark = pytest.mark.integration

_MOSQUITTO_CMD = ("sh -c \"printf 'listener 1883 0.0.0.0\\nallow_anonymous true\\n' "
                  "> /mosquitto/config/mosquitto.conf && exec mosquitto -c /mosquitto/config/mosquitto.conf\"")


@pytest.fixture(scope="module")
def broker():
    """Boot one Mosquitto broker for the module; yields (host, port).

    Mosquitto 2.x refuses connections without an explicit anonymous listener, so the command
    writes a minimal config on startup before exec'ing the broker.
    """
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = DockerContainer("eclipse-mosquitto:2").with_exposed_ports(1883).with_command(_MOSQUITTO_CMD)
    container.start()
    try:
        wait_for_logs(container, "running", timeout=30)
        yield container.get_container_host_ip(), int(container.get_exposed_port(1883))
    finally:
        container.stop()


def _manifest(streams: list[tuple[str, str, dict]]):
    mb = ManifestBuilder.from_app_yaml().add_asset("pump-1")
    for name, dtype, cfg in streams:
        mb = mb.add_input(name, dtype, configuration=cfg)
    return mb.set_configuration({"mqtt": {"host": "unused-in-test"}}).build()


def _ns() -> str:
    """A unique topic namespace so tests sharing the module broker can't collide."""
    return uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def _fresh_command_queue():
    """Give each test its own command queue.

    `main._commands` is created at import and binds to whichever event loop first awaits it
    (`_command_loop` inside `_consume`). pytest-asyncio runs each test in a fresh loop, so a
    queue bound by one test raises "bound to a different event loop" in the next. `on_control_change`
    and `_command_loop` both resolve the module global at call time, so replacing it here is enough.
    """
    main._commands = asyncio.Queue(maxsize=main._MAX_QUEUED_COMMANDS)
    yield


def _connected_event(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Set an event when `_consume` logs "Connected to MQTT" (i.e. it has finished subscribing).

    `_consume` owns its client and subscribes internally, so there's no return value to await;
    the connector's own log line is the readiness signal. Waiting on it lets a QoS-2 test publish
    exactly once *after* the subscription exists, so exactly-once delivery is actually observable.
    """
    ev = asyncio.Event()
    original = main.logger.info

    def info(msg, *args, **kwargs):
        if msg == "Connected to MQTT":
            ev.set()
        return original(msg, *args, **kwargs)

    monkeypatch.setattr(main.logger, "info", info)
    return ev


async def _wait_connected(consumer: asyncio.Task, connected: asyncio.Event, timeout: float = 15) -> None:
    """Wait until `_consume` has subscribed, surfacing an early consumer failure instead of hanging."""
    async with asyncio.timeout(timeout):
        while not connected.is_set():
            if consumer.done():
                consumer.result()   # re-raise the real connect error rather than time out
            await asyncio.sleep(0.05)


async def _await(condition, consumer: asyncio.Task, timeout: float = 15) -> None:
    """Poll `condition` until true, re-raising a consumer crash instead of masking it as a timeout."""
    async with asyncio.timeout(timeout):
        while not condition():
            if consumer.done():
                consumer.result()
            await asyncio.sleep(0.05)


async def _stop(consumer: asyncio.Task) -> None:
    """Tear the background consumer down; cancellation unwinds its TaskGroup and closes the client."""
    consumer.cancel()
    # Teardown only: cancelling a TaskGroup can surface CancelledError or a BaseExceptionGroup.
    # Real consumer failures are surfaced earlier by `_wait_connected`/`_await` via `.result()`.
    with contextlib.suppress(BaseException):
        await consumer


def _by_stream(outputs) -> dict:
    """Map data_stream -> payload for the data outputs (skips acks and other non-datastream msgs)."""
    return {o.resource.data_stream: o.payload
            for o in outputs
            if isinstance(o.resource, KRNAssetDataStream)}


@pytest.mark.asyncio
async def test_consume_message_publishes_to_kelvin(broker: tuple[str, int]) -> None:
    """Publish to a mapped topic; the connector receives it and publishes a Number into Kelvin."""
    host, port = broker
    manifest = _manifest([("temperature", "number", {"topic": "telemetry"})])

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        topic_map = main.build_topic_map(main.app.assets)
        async with aiomqtt.Client(hostname=host, port=port) as client:
            await client.subscribe("telemetry")
            async with aiomqtt.Client(hostname=host, port=port) as publisher:
                await publisher.publish("telemetry", payload=b"42.5")
            # _read_loop never returns on a live broker, so run it as a task and stop as soon as
            # the first Kelvin output lands instead of burning the full timeout.
            reader = asyncio.create_task(main._read_loop(client, topic_map))
            try:
                async with asyncio.timeout(15):
                    while not harness.outputs:
                        if reader.done():
                            reader.result()   # re-raise the loop's real error instead of masking it behind the timeout
                        await asyncio.sleep(0.05)
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
        out = harness.outputs

    assert any(o.resource.data_stream == "temperature" and o.payload == 42.5 for o in out)


@pytest.mark.asyncio
async def test_control_writeback_lands_on_topic(broker: tuple[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """A control change is published (QoS 1) to its control_topic and acked `processed`."""
    host, port = broker
    acks: list = []
    monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())

    async with aiomqtt.Client(hostname=host, port=port) as verifier:
        await verifier.subscribe("plant/pump-1/cmd")
        async with aiomqtt.Client(hostname=host, port=port) as client:
            cc = SimpleNamespace(resource=KRNAssetDataStream("pump-1", "setpoint"), id=uuid.uuid4(),
                                 payload=SimpleNamespace(payload=55))
            await main._handle_command(client, {("pump-1", "setpoint"): "plant/pump-1/cmd"}, cc)
        msg = await asyncio.wait_for(anext(aiter(verifier.messages)), timeout=15)

    assert msg.payload == b"55"
    assert acks[0].payload.state == StateEnum.processed


@pytest.mark.asyncio
async def test_consume_both_directions_qos2(broker: tuple[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the full `_consume` loop both ways over one shared client, at QoS 2.

    Inbound: a single QoS-2 publish lands as exactly one Kelvin Number (exactly-once delivery).
    Outbound: a control change enqueued via `on_control_change` is published to its control_topic
    (a verifier client reads it) and acked `processed`. This exercises the real subscribe wiring
    (the configured QoS reaching the broker) plus the read and command loops together.
    """
    host, port = broker
    ns = _ns()
    in_topic = f"{ns}/telemetry"
    cmd_topic = f"{ns}/plant/pump-1/cmd"
    manifest = _manifest([
        ("temperature", "number", {"topic": in_topic}),
        ("setpoint", "number", {"control_topic": cmd_topic}),
    ])
    connected = _connected_event(monkeypatch)
    settings = main.Settings(mqtt={"host": host, "port": port}, qos=2)

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        async with aiomqtt.Client(hostname=host, port=port) as verifier:
            await verifier.subscribe(cmd_topic, qos=1)
            consumer = asyncio.create_task(main._consume(settings))
            try:
                await _wait_connected(consumer, connected)

                # Inbound: subscription is live, so a single publish yields a single delivery.
                async with aiomqtt.Client(hostname=host, port=port) as publisher:
                    await publisher.publish(in_topic, payload=b"42.5", qos=2)

                # Outbound: enqueue a control change; the shared command loop writes it back.
                cc = SimpleNamespace(resource=KRNAssetDataStream("pump-1", "setpoint"), id=uuid.uuid4(),
                                     payload=SimpleNamespace(payload=55))
                await main.on_control_change(cc)
                cmd_msg = await asyncio.wait_for(anext(aiter(verifier.messages)), timeout=15)

                def _both_landed() -> bool:
                    nums = [o for o in harness.outputs
                            if isinstance(o.resource, KRNAssetDataStream) and o.resource.data_stream == "temperature"]
                    acks = [o for o in harness.outputs
                            if isinstance(o, ControlChangeStatus) and o.payload.state == StateEnum.processed]
                    return bool(nums) and bool(acks)

                await _await(_both_landed, consumer)
            finally:
                await _stop(consumer)
        out = harness.outputs

    nums = [o for o in out
            if isinstance(o.resource, KRNAssetDataStream) and o.resource.data_stream == "temperature"]
    assert len(nums) == 1 and nums[0].payload == 42.5   # QoS 2: exactly-once, not duplicated
    assert cmd_msg.payload == b"55"
    assert any(isinstance(o, ControlChangeStatus) and o.payload.state == StateEnum.processed for o in out)


@pytest.mark.asyncio
async def test_wildcard_and_templated_topics_fan_out(broker: tuple[str, int],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """A `+` wildcard filter and a `{asset}/{stream}` templated filter both subscribe for real; the
    broker (not the client-side `topic.matches`) delivers matching topics to the right stream."""
    host, port = broker
    ns = _ns()
    wild_filter = f"{ns}/plant/+/readings"
    templated = f"{ns}/sensors/{{asset}}/{{stream}}"   # resolves to {ns}/sensors/pump-1/flow
    manifest = _manifest([
        ("pressure", "number", {"topic": wild_filter}),
        ("flow", "number", {"topic": templated}),
    ])
    connected = _connected_event(monkeypatch)
    settings = main.Settings(mqtt={"host": host, "port": port}, qos=1)

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        consumer = asyncio.create_task(main._consume(settings))
        try:
            await _wait_connected(consumer, connected)
            async with aiomqtt.Client(hostname=host, port=port) as publisher:
                await publisher.publish(f"{ns}/plant/pump-1/readings", payload=b"4.2", qos=1)
                await publisher.publish(f"{ns}/sensors/pump-1/flow", payload=b"7.7", qos=1)

            await _await(lambda: {"pressure", "flow"} <= _by_stream(harness.outputs).keys(), consumer)
        finally:
            await _stop(consumer)
        out = _by_stream(harness.outputs)

    assert out["pressure"] == 4.2   # wildcard `+` matched the pump-1 segment
    assert out["flow"] == 7.7       # templated {asset}/{stream} resolved to a concrete topic


@pytest.mark.asyncio
async def test_multi_type_payloads_round_trip(broker: tuple[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """Real bytes for number/string/boolean/object (+ a payload_field extraction) subscribe for
    real and each arrive as the natively-typed Kelvin value."""
    host, port = broker
    ns = _ns()
    topics = {
        "temperature": f"{ns}/num",
        "status": f"{ns}/str",
        "enabled": f"{ns}/bool",
        "readings": f"{ns}/obj",
        "pressure": f"{ns}/nested",
    }
    manifest = _manifest([
        ("temperature", "number", {"topic": topics["temperature"]}),
        ("status", "string", {"topic": topics["status"]}),
        ("enabled", "boolean", {"topic": topics["enabled"]}),
        ("readings", "object", {"topic": topics["readings"]}),
        ("pressure", "number", {"topic": topics["pressure"], "payload_field": "readings.pressure"}),
    ])
    connected = _connected_event(monkeypatch)
    settings = main.Settings(mqtt={"host": host, "port": port}, qos=1)

    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        consumer = asyncio.create_task(main._consume(settings))
        try:
            await _wait_connected(consumer, connected)
            async with aiomqtt.Client(hostname=host, port=port) as publisher:
                await publisher.publish(topics["temperature"], payload=b"42.5", qos=1)
                await publisher.publish(topics["status"], payload=b"running", qos=1)
                await publisher.publish(topics["enabled"], payload=b"true", qos=1)
                await publisher.publish(topics["readings"], payload=b'{"p": 1}', qos=1)
                await publisher.publish(topics["pressure"], payload=b'{"readings": {"pressure": 4.2}}', qos=1)

            await _await(lambda: set(topics) <= _by_stream(harness.outputs).keys(), consumer)
        finally:
            await _stop(consumer)
        out = _by_stream(harness.outputs)

    assert out["temperature"] == 42.5 and isinstance(out["temperature"], float)
    assert out["status"] == "running" and isinstance(out["status"], str)
    assert out["enabled"] is True
    assert out["readings"] == {"p": 1}
    assert out["pressure"] == 4.2      # extracted from the nested JSON payload_field


@pytest.mark.asyncio
async def test_retained_message_ingested_on_subscribe(broker: tuple[str, int],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """A retained message published *before* the connector subscribes is delivered on connect and
    ingested. This is the one case the client-side fake can't reproduce; only a real broker retains
    and replays it to a fresh subscriber."""
    host, port = broker
    ns = _ns()
    topic = f"{ns}/retained/level"
    manifest = _manifest([("level", "number", {"topic": topic})])
    connected = _connected_event(monkeypatch)
    settings = main.Settings(mqtt={"host": host, "port": port}, qos=1)

    # Retain BEFORE the connector exists, then let its subscribe pull the retained value.
    async with aiomqtt.Client(hostname=host, port=port) as seeder:
        await seeder.publish(topic, payload=b"99.9", qos=1, retain=True)

    try:
        async with KelvinAppTest(main.app, manifest=manifest) as harness:
            consumer = asyncio.create_task(main._consume(settings))
            try:
                await _wait_connected(consumer, connected)
                await _await(lambda: "level" in _by_stream(harness.outputs), consumer)
            finally:
                await _stop(consumer)
            out = _by_stream(harness.outputs)
        assert out["level"] == 99.9
    finally:
        # Clear the retained message so the module-shared broker doesn't keep it around.
        async with aiomqtt.Client(hostname=host, port=port) as cleaner:
            await cleaner.publish(topic, payload=b"", qos=1, retain=True)


async def _noop() -> None:
    return None
