"""Unit tests for the Kafka connector's pure helpers + control-writeback handling."""
import asyncio
import contextlib
import uuid
from types import SimpleNamespace

import pytest
from aiokafka.errors import KafkaError
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message.base_messages import StateEnum
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from settings import Settings


def _type(primitive: str) -> SimpleNamespace:
    return SimpleNamespace(primitive=primitive)


def _assets(mapping: dict) -> dict:
    """Fake `app.assets`: {asset: {stream: (primitive, configuration_dict)}}."""
    return {
        asset: SimpleNamespace(datastreams={
            stream: SimpleNamespace(datastream=SimpleNamespace(type=_type(prim)), configuration=cfg)
            for stream, (prim, cfg) in streams.items()
        })
        for asset, streams in mapping.items()
    }


class TestDecodeValue:
    def test_number_and_string(self) -> None:
        assert main.decode_value(b"42.5", "number", None) == 42.5
        assert main.decode_value(b"running", "string", None) == "running"

    def test_object_whole_value(self) -> None:
        assert main.decode_value(b'{"a": 1}', "object", None) == {"a": 1}

    @pytest.mark.parametrize("raw,expected", [(b"true", True), (b"false", False), (b"0", False)])
    def test_boolean_parsed(self, raw: bytes, expected: bool) -> None:
        assert main.decode_value(raw, "boolean", None) is expected

    def test_payload_field_nested(self) -> None:
        assert main.decode_value(b'{"r": {"p": 4.2}}', "number", "r.p") == 4.2

    def test_missing_field_raises(self) -> None:
        with pytest.raises(KeyError):
            main.decode_value(b'{"a": 1}', "number", "b")


class TestResolve:
    def test_substitutes(self) -> None:
        assert main.resolve("telemetry.{asset}", "pump-1", "t") == "telemetry.pump-1"


class TestBuildTopicMap:
    def test_resolves_topic_key_and_field(self) -> None:
        assets = _assets({"pump-1": {"temp": ("number",
                          {"topic": "telemetry.{asset}", "payload_field": "temperature", "key": "{asset}"})}})
        tm = main.build_topic_map(assets)
        (m,) = tm["telemetry.pump-1"]
        assert m.asset == "pump-1" and m.payload_field == "temperature" and m.key == "pump-1"

    def test_skips_without_topic(self) -> None:
        assert main.build_topic_map(_assets({"a": {"s": ("number", {})}})) == {}

    def test_warns_on_fully_unconfigured_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stream with neither topic nor control_topic can't do anything; warn per stream."""
        warnings: list[dict] = []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append(kw))
        assert main.build_topic_map(_assets({"pump-1": {"orphan": ("number", {})}})) == {}
        assert warnings == [{"asset": "pump-1", "stream": "orphan"}]

    def test_writeback_only_stream_is_skipped_without_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """control_topic without topic is a valid writeback-only mapping; no warning."""
        warnings: list = []
        monkeypatch.setattr(main.logger, "warning", lambda *a, **kw: warnings.append(a))
        assert main.build_topic_map(_assets({"a": {"sp": ("number", {"control_topic": "cmd.a"})}})) == {}
        assert warnings == []

    def test_missing_primitive_warns_at_build_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        warnings: list = []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append((msg, kw)))
        assets = _assets({"pump-1": {"temp": (None, {"topic": "telemetry"})}})  # type without primitive

        tm = main.build_topic_map(assets)

        (m,) = tm["telemetry"]
        assert main.primitive_name(m.msg_type) == "string"                      # fallback still applies
        assert len(warnings) == 1 and warnings[0][1] == {"asset": "pump-1", "stream": "temp"}

    def test_usable_primitive_does_not_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        warnings: list = []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append(msg))
        main.build_topic_map(_assets({"pump-1": {"temp": ("number", {"topic": "telemetry"})}}))
        assert warnings == []


class TestBuildCommandMap:
    def test_resolves_control_topic(self) -> None:
        assets = _assets({"pump-1": {"sp": ("number", {"topic": "x", "control_topic": "cmd.{asset}"})}})
        assert main.build_command_map(assets) == {("pump-1", "sp"): "cmd.pump-1"}


class TestMatchTargets:
    def test_exact_topic_and_no_key_filter(self) -> None:
        m = main.StreamMapping("a", "temp", _type("number"), None, None)
        assert main.match_targets("telemetry", "anything", {"telemetry": [m]}) == [m]

    def test_key_filter_matches(self) -> None:
        m = main.StreamMapping("pump-1", "temp", _type("number"), None, "pump-1")
        assert main.match_targets("telemetry", "pump-1", {"telemetry": [m]}) == [m]

    def test_key_filter_excludes_other_keys(self) -> None:
        m = main.StreamMapping("pump-1", "temp", _type("number"), None, "pump-1")
        assert main.match_targets("telemetry", "pump-2", {"telemetry": [m]}) == []

    def test_other_topic_no_match(self) -> None:
        m = main.StreamMapping("a", "temp", _type("number"), None, None)
        assert main.match_targets("other", None, {"telemetry": [m]}) == []


class _FakeConsumer:
    """Async-iterable consumer over canned records (auto-commit handled by aiokafka)."""
    def __init__(self, records: list) -> None:
        self._records = records

    def __aiter__(self) -> "_FakeConsumer":
        self._it = iter(self._records)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _record(value: object, key: object, topic: str) -> SimpleNamespace:
    return SimpleNamespace(value=value, key=key, topic=topic)


@pytest.mark.asyncio
class TestReadLoop:
    @pytest.fixture(autouse=True)
    def _passthrough_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Skip pydantic Message validation; capture (resource, payload) the loop would publish.
        monkeypatch.setattr(main, "Message",
                            lambda type, resource, payload: SimpleNamespace(resource=resource, payload=payload))

    async def test_binary_key_still_publishes_to_unkeyed_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        published: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: published.append(m) or _noop())
        m = main.StreamMapping("pump-1", "temp", _type("number"), None, None)   # key=None => any key
        consumer = _FakeConsumer([_record(b"42.5", b"\xff\xfe", "telemetry")])  # undecodable key

        await main._read_loop(consumer, {"telemetry": [m]})

        assert [p.payload for p in published] == [42.5]

    async def test_skips_tombstone_and_unmatched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        published: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: published.append(m) or _noop())
        consumer = _FakeConsumer([
            _record(None, None, "telemetry"),          # tombstone
            _record(b"1", None, "unmapped"),           # no matching target
        ])

        await main._read_loop(consumer, {})

        assert published == []                          # nothing published, loop doesn't crash


@pytest.mark.asyncio
class TestControlWriteback:
    class _FakeProducer:
        def __init__(self) -> None:
            self.sent: list[tuple] = []

        async def send_and_wait(self, topic, value, key):
            self.sent.append((topic, value, key))

    class _FailingProducer:
        async def send_and_wait(self, topic, value, key):
            raise KafkaError("broker unavailable")

    def _cc(self, asset: str, stream: str, value: object):
        return SimpleNamespace(resource=KRNAssetDataStream(asset, stream), id=uuid.uuid4(),
                               payload=SimpleNamespace(payload=value))

    async def test_enqueue_and_produce_and_ack(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        producer = self._FakeProducer()
        msg = self._cc("pump-1", "setpoint", 55)

        await main.on_control_change(msg)
        assert main._commands.qsize() == 1
        queued = await main._commands.get()
        await main._handle_command(producer, {("pump-1", "setpoint"): "cmd.pump-1"}, queued)

        assert producer.sent == [("cmd.pump-1", b"55", b"pump-1")]
        assert len(acks) == 1 and acks[0].payload.state == StateEnum.processed

    async def test_non_datastream_resource_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        producer = self._FakeProducer()
        msg = SimpleNamespace(resource=KRNAsset("pump-1"), id=uuid.uuid4(),
                              payload=SimpleNamespace(payload=55))

        await main._handle_command(producer, {}, msg)

        assert producer.sent == []
        assert len(acks) == 1 and acks[0].payload.state == StateEnum.failed

    async def test_unmapped_control_topic_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stream with no mapped control_topic can't be written back, so the command gets a
        terminal `failed` ack (not `rejected`) and the producer is never touched."""
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        producer = self._FakeProducer()
        await main._handle_command(producer, {}, self._cc("pump-1", "setpoint", 55))
        assert producer.sent == [] and acks[0].payload.state == StateEnum.failed

    async def test_unmapped_control_topic_failed_without_producer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The terminal `failed` ack for an unmapped control_topic needs no producer at all
        (ingest-only config passes producer=None to the command loop)."""
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        await main._handle_command(None, {}, self._cc("pump-1", "setpoint", 55))
        assert acks[0].payload.state == StateEnum.failed and "no control_topic" in acks[0].payload.message

    async def test_produce_failure_acks_failed_and_reraises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        with pytest.raises(KafkaError):
            await main._handle_command(self._FailingProducer(), {("pump-1", "setpoint"): "cmd.pump-1"},
                                       self._cc("pump-1", "setpoint", 55))
        assert acks[0].payload.state == StateEnum.failed

    async def test_produce_failure_keeps_command_loop_running(
            self, monkeypatch: pytest.MonkeyPatch, _fresh_command_state: None) -> None:
        """A transient produce failure acks the command `failed` and the command loop keeps
        serving the queue; it must NOT propagate out (which would tear down the consumer)."""
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        monkeypatch.setattr(main, "_PRODUCER_ERROR_BACKOFF", 0)

        class _FlakyProducer:
            def __init__(self) -> None:
                self.sent: list[tuple] = []

            async def send_and_wait(self, topic, value, key):
                if not self.sent and topic == "cmd.pump-1":
                    self.sent.append(("attempt", topic))
                    raise KafkaError("transient outage")     # first command fails
                self.sent.append((topic, value, key))        # second succeeds

        cmap = {("pump-1", "setpoint"): "cmd.pump-1"}
        main._commands.put_nowait(self._cc("pump-1", "setpoint", 1))
        main._commands.put_nowait(self._cc("pump-1", "setpoint", 2))

        task = asyncio.create_task(main._command_loop(_FlakyProducer(), cmap))
        try:
            await _poll_until(lambda: len(acks) >= 2)        # wait for both commands to be acked
            assert not task.done()                           # loop survived the produce failure
        finally:
            await _cancel(task)

        states = [a.payload.state for a in acks]
        assert states[0] == StateEnum.failed                 # first command acked failed
        assert StateEnum.processed in states                 # second command still got through

    async def test_full_queue_rejects_with_failed_ack(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=1))
        main._commands.put_nowait(self._cc("pump-1", "setpoint", 1))   # fill it

        await main.on_control_change(self._cc("pump-1", "setpoint", 2))

        assert main._commands.qsize() == 1                              # rejected, not enqueued
        assert acks[0].payload.state == StateEnum.failed

    async def test_cancellation_mid_command_acks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Teardown between dequeue and completion must still leave a terminal (failed) ack
        for the in-flight command, so the platform never waits on it forever."""
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=10))
        entered = asyncio.Event()

        async def _stuck_handle(producer, command_map, msg) -> None:
            entered.set()
            await asyncio.Event().wait()                               # block until cancelled

        monkeypatch.setattr(main, "_handle_command", _stuck_handle)
        main._commands.put_nowait(self._cc("pump-1", "setpoint", 55))

        task = asyncio.create_task(main._command_loop(self._FakeProducer(), {}))
        await entered.wait()                                           # command is in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(acks) == 1 and acks[0].payload.state == StateEnum.failed
        assert "shutting down" in acks[0].payload.message


class TestClientKwargs:
    """_client_kwargs: protocol/SASL/TLS wiring shared with the Kafka exporter."""

    @pytest.mark.asyncio
    async def test_plaintext_has_no_ssl_or_sasl(self) -> None:
        kw = await main._client_kwargs(Settings().kafka)
        assert kw["security_protocol"] == "PLAINTEXT"
        assert "ssl_context" not in kw and "sasl_mechanism" not in kw

    @pytest.mark.asyncio
    async def test_sasl_ssl_carries_credentials_and_context(self) -> None:
        import ssl
        kw = await main._client_kwargs(Settings(kafka={"security": {
            "protocol": "SASL_SSL",
            "sasl": {"mechanism": "PLAIN", "username": "u", "password": "p"}}}).kafka)
        assert kw["security_protocol"] == "SASL_SSL"
        assert kw["sasl_mechanism"] == "PLAIN" and kw["sasl_plain_username"] == "u"
        assert kw["sasl_plain_password"] == "p"                       # SecretStr unwrapped for aiokafka
        assert isinstance(kw["ssl_context"], ssl.SSLContext)

    def test_mtls_material_flows_through_temp_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cert/key PEM content is written to temp files for load_cert_chain, then deleted."""
        from pathlib import Path
        loaded: dict = {}

        class _Ctx:
            def load_cert_chain(self, certfile: str, keyfile: str) -> None:
                loaded["cert"] = Path(certfile).read_text()
                loaded["key"] = Path(keyfile).read_text()
                loaded["paths"] = (certfile, keyfile)

        monkeypatch.setattr(main.ssl, "create_default_context", lambda cadata=None: _Ctx())
        tls = Settings(kafka={"security": {"protocol": "SSL",
                                           "tls": {"client_cert": "CERT-PEM",
                                                   "client_key": "KEY-PEM"}}}).kafka.security.tls

        main._build_ssl_context(tls)

        assert loaded["cert"] == "CERT-PEM" and loaded["key"] == "KEY-PEM"
        for path in loaded["paths"]:                      # PEM material must not linger on disk
            assert not Path(path).exists()


@pytest.mark.asyncio
class TestConsumeShutdown:
    async def test_producer_stopped_even_if_consumer_stop_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stopped: list[str] = []

        class _Consumer:
            def __init__(self, *topics, **kw) -> None: ...
            async def start(self) -> None:
                raise KafkaError("broker unavailable")
            async def stop(self) -> None:
                stopped.append("consumer")
                raise KafkaError("consumer stop failed")

        class _Producer:
            def __init__(self, **kw) -> None: ...
            async def start(self) -> None: ...
            async def stop(self) -> None:
                stopped.append("producer")

        # A stream with both topic and control_topic builds both clients, so finally must stop both.
        assets = _assets({"pump-1": {"sp": ("number", {"topic": "t", "control_topic": "cmd.pump-1"})}})
        monkeypatch.setattr(main, "AIOKafkaConsumer", _Consumer)
        monkeypatch.setattr(main, "AIOKafkaProducer", _Producer)
        monkeypatch.setattr(type(main.app), "assets", property(lambda self: assets))

        with pytest.raises(KafkaError, match="broker unavailable"):
            await main._consume(Settings(kafka={"bootstrap_servers": "b:9092", "group_id": "g"}))

        assert stopped == ["consumer", "producer"]      # producer stopped despite consumer.stop raising


# --- shared scaffolding for the _consume readiness tests ------------------------------------
# TestConsumeClientSelection and TestConsumeMappingLogs both start _consume, wait for its
# readiness signal, then cancel. The queue/event reset and the readiness wait live here once
# so both classes share one implementation.

@pytest.fixture
def _fresh_command_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own _commands queue and _started event.

    The module-level ones bind to the first event loop that touches them; a fresh pair per test
    keeps _consume/_command_loop from crossing loops between tests."""
    monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=main._MAX_QUEUED_COMMANDS))
    monkeypatch.setattr(main, "_started", asyncio.Event())


async def _await_started(consume_task: "asyncio.Task", timeout: float = 5.0) -> None:
    """Wait until _consume signals readiness (_started) OR its task finishes first.

    A bare `await main._started.wait()` hangs forever if a regression makes _consume raise
    BEFORE calling `_started.set()` (there is no pytest-timeout in this repo). Race the
    started-event wait against the consume task: whichever finishes first wins. If the task
    finished, call `.result()` to re-raise its real exception instead of masking it behind a
    generic timeout; the modest timeout is only a backstop so a truly stuck test still fails."""
    started_wait = asyncio.ensure_future(main._started.wait())
    done, _ = await asyncio.wait({started_wait, consume_task}, timeout=timeout,
                                 return_when=asyncio.FIRST_COMPLETED)
    if not started_wait.done():
        started_wait.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await started_wait
    if consume_task in done:
        consume_task.result()                          # re-raise the real error _consume raised
    if not done:
        raise TimeoutError("timed out waiting for _consume readiness")   # backstop, not the norm


async def _poll_until(predicate, *, tries: int = 200, delay: float = 0.01) -> None:
    """Poll `predicate` up to `tries` times, sleeping `delay` between checks.

    A bounded, deterministic replacement for a wall-clock wait: the background loops under test
    would run forever, so we watch a condition (e.g. acks arrived) instead of sleeping a fixed span."""
    for _ in range(tries):
        if predicate():
            break
        await asyncio.sleep(delay)


async def _cancel(task: "asyncio.Task") -> None:
    """Cancel a task and await its unwind, swallowing the expected CancelledError."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _run_briefly() -> None:
    """Start _consume, wait for readiness (or surface an early failure), then cancel.

    Deterministic: no wall-clock wait — the loops would otherwise run forever. The mapping
    diagnostics have already logged by the time _started fires (it is set inside the task group)."""
    task = asyncio.create_task(
        main._consume(Settings(kafka={"bootstrap_servers": "b:9092", "group_id": "g"})))
    await _await_started(task)
    await _cancel(task)


@pytest.mark.asyncio
class TestConsumeClientSelection:
    """_consume only builds the clients it needs: no topics => no consumer, no commands => no producer.
    Whatever it does build must still be started and stopped."""

    @pytest.fixture(autouse=True)
    def _fresh_command_queue(self, _fresh_command_state: None) -> None:
        # Per-test _commands queue + _started event via the shared module-level fixture.
        pass

    @staticmethod
    def _stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
        events: dict[str, list] = {"consumer": [], "producer": []}

        class _Consumer:
            def __init__(self, *topics, **kw) -> None:
                events["consumer"].append("init")
            async def start(self) -> None:
                events["consumer"].append("start")
            async def stop(self) -> None:
                events["consumer"].append("stop")
            def __aiter__(self) -> "_Consumer":
                return self
            async def __anext__(self):
                await asyncio.Event().wait()               # live consumer: block for records

        class _Producer:
            def __init__(self, **kw) -> None:
                events["producer"].append("init")
            async def start(self) -> None:
                events["producer"].append("start")
            async def stop(self) -> None:
                events["producer"].append("stop")

        monkeypatch.setattr(main, "AIOKafkaConsumer", _Consumer)
        monkeypatch.setattr(main, "AIOKafkaProducer", _Producer)
        return events

    @pytest.mark.parametrize("stream_cfg, built_client, skipped_client", [
        ({"topic": "telemetry"}, "consumer", "producer"),        # ingest only => consumer, no producer
        ({"control_topic": "cmd.pump-1"}, "producer", "consumer"),  # writeback only => producer, no consumer
    ])
    async def test_builds_only_the_client_it_needs(
            self, monkeypatch: pytest.MonkeyPatch,
            stream_cfg: dict, built_client: str, skipped_client: str) -> None:
        events = self._stub(monkeypatch)
        assets = _assets({"pump-1": {"sp": ("number", stream_cfg)}})
        monkeypatch.setattr(type(main.app), "assets", property(lambda self: assets))

        await _run_briefly()

        assert events[built_client] == ["init", "start", "stop"]   # client used and torn down
        assert events[skipped_client] == []                        # other client never built

    async def test_ingest_only_control_change_acked_failed_without_producer(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: an ingest-only config (mapped topic, no control_topic) builds no producer,
        but a control change still arrives on the queue. The always-on command loop must drain it
        and ack it terminally `failed` instead of leaving the platform waiting forever."""
        events = self._stub(monkeypatch)
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        assets = _assets({"pump-1": {"temp": ("number", {"topic": "telemetry"})}})  # ingest only
        monkeypatch.setattr(type(main.app), "assets", property(lambda self: assets))
        main._commands.put_nowait(SimpleNamespace(
            resource=KRNAssetDataStream("pump-1", "temp"), id=uuid.uuid4(),
            payload=SimpleNamespace(payload=1)))

        task = asyncio.create_task(
            main._consume(Settings(kafka={"bootstrap_servers": "b:9092", "group_id": "g"})))
        try:
            await _await_started(task)               # readiness, or surface an early _consume failure
            await _poll_until(lambda: bool(acks))    # let the command loop drain + ack
        finally:
            await _cancel(task)

        assert events["producer"] == []                          # ingest-only: producer never built
        assert len(acks) == 1 and acks[0].payload.state == StateEnum.failed
        assert "no control_topic" in acks[0].payload.message


@pytest.mark.asyncio
class TestAwaitStarted:
    """The shared readiness helper must not hang when _consume dies before signalling _started;
    it surfaces the real exception instead (there is no pytest-timeout in this repo)."""

    async def test_surfaces_real_error_when_consume_raises_before_started(
            self, monkeypatch: pytest.MonkeyPatch, _fresh_command_state: None) -> None:
        # Consumer.start raises, so _consume propagates BEFORE reaching `_started.set()`.
        class _Consumer:
            def __init__(self, *topics, **kw) -> None: ...
            async def start(self) -> None:
                raise KafkaError("broker down before readiness")
            async def stop(self) -> None: ...

        monkeypatch.setattr(main, "AIOKafkaConsumer", _Consumer)
        assets = _assets({"pump-1": {"temp": ("number", {"topic": "telemetry"})}})  # needs a consumer
        monkeypatch.setattr(type(main.app), "assets", property(lambda self: assets))

        task = asyncio.create_task(
            main._consume(Settings(kafka={"bootstrap_servers": "b:9092", "group_id": "g"})))
        with pytest.raises(KafkaError, match="broker down before readiness"):
            await _await_started(task)               # real error, not a hang or a TimeoutError
        assert not main._started.is_set()

    async def test_returns_once_started_is_set(self, _fresh_command_state: None) -> None:
        async def _ready_then_block() -> None:
            main._started.set()
            await asyncio.Event().wait()             # stay alive so the wait races a live task

        task = asyncio.create_task(_ready_then_block())
        try:
            await _await_started(task)               # returns on the started event, task still running
            assert main._started.is_set() and not task.done()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
class TestConsumeMappingLogs:
    """_consume's mapping diagnostics: warn only when nothing is mapped at all;
    a writeback-only config (control_topic without topic) is valid and logs info.

    The mapping diagnostics run before the task group, so we let _consume start, await its
    readiness Event, then cancel it; the loops themselves would otherwise run forever."""

    class _Consumer:
        def __init__(self, *topics, **kw) -> None: ...
        async def start(self) -> None: ...
        async def stop(self) -> None: ...

    class _Producer:
        def __init__(self, **kw) -> None: ...
        async def start(self) -> None: ...
        async def stop(self) -> None: ...

    @pytest.fixture(autouse=True)
    def _stub_clients(self, monkeypatch: pytest.MonkeyPatch, _fresh_command_state: None) -> None:
        # Stub the clients here; the fresh per-test _commands/_started come from the shared fixture.
        monkeypatch.setattr(main, "AIOKafkaConsumer", self._Consumer)
        monkeypatch.setattr(main, "AIOKafkaProducer", self._Producer)

    @staticmethod
    def _capture_logs(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
        warnings, infos = [], []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append(msg))
        monkeypatch.setattr(main.logger, "info", lambda msg, **kw: infos.append(msg))
        return warnings, infos

    async def test_nothing_mapped_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(type(main.app), "assets", property(lambda self: {}))
        warnings, infos = self._capture_logs(monkeypatch)

        await _run_briefly()

        assert any("No streams mapped" in w for w in warnings)
        assert not any("writeback-only" in i for i in infos)

    async def test_writeback_only_logs_info_not_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _assets({"pump-1": {"setpoint": ("number", {"control_topic": "cmd.pump-1"})}})
        monkeypatch.setattr(type(main.app), "assets", property(lambda self: assets))
        warnings, infos = self._capture_logs(monkeypatch)

        await _run_briefly()

        assert warnings == []                              # valid deployment, no warning
        assert any("writeback-only" in i for i in infos)


@pytest.mark.asyncio
class TestPublishHarness:
    """End-to-end via the SDK harness: a record flows through the real Message publish path
    (real KMessageType from app.assets), and we assert on the captured Kelvin output."""

    @staticmethod
    def _manifest(streams: list[tuple[str, str, dict]]):
        mb = ManifestBuilder.from_app_yaml().add_asset("pump-1")
        for name, dtype, cfg in streams:
            mb = mb.add_input(name, dtype, configuration=cfg)
        return mb.set_configuration({"kafka": {"bootstrap_servers": "b:9092", "group_id": "g"}}).build()

    async def test_each_type_publishes_with_native_value(self) -> None:
        manifest = self._manifest([
            ("temperature", "number", {"topic": "t.num"}),
            ("status", "string", {"topic": "t.str"}),
            ("enabled", "boolean", {"topic": "t.bool"}),
            ("readings", "object", {"topic": "t.obj"}),
        ])
        async with KelvinAppTest(main.app, manifest=manifest) as harness:
            topic_map = main.build_topic_map(main.app.assets)
            await main._read_loop(_FakeConsumer([
                _record(b"42.5", None, "t.num"),
                _record(b"running", None, "t.str"),
                _record(b"true", None, "t.bool"),
                _record(b'{"p": 1}', None, "t.obj"),
            ]), topic_map)
            out = {o.resource.data_stream: o.payload for o in harness.outputs}

        assert out["temperature"] == 42.5
        assert out["status"] == "running"
        assert out["enabled"] is True
        assert out["readings"] == {"p": 1}

    async def test_payload_field_extraction_publishes_scalar(self) -> None:
        manifest = self._manifest([("pressure", "number", {"topic": "t.readings", "payload_field": "readings.pressure"})])
        async with KelvinAppTest(main.app, manifest=manifest) as harness:
            topic_map = main.build_topic_map(main.app.assets)
            await main._read_loop(_FakeConsumer([_record(b'{"readings": {"pressure": 4.2}}', None, "t.readings")]),
                                  topic_map)
            out = harness.outputs

        assert len(out) == 1 and out[0].payload == 4.2 and out[0].resource.data_stream == "pressure"


async def _noop() -> None:
    return None


class TestLeafExceptions:
    """The reconnect handler logs every failure in the group, including nested groups."""

    def test_flattens_siblings(self) -> None:
        eg = ExceptionGroup("boom", [KafkaError("a"), OSError("b")])
        leaves = main._leaf_exceptions(eg)
        assert [type(e).__name__ for e in leaves] == ["KafkaError", "OSError"]

    def test_flattens_nested_groups(self) -> None:
        inner = [OSError("b"), ValueError("c")]
        eg = ExceptionGroup("outer", [KafkaError("a"), ExceptionGroup("inner", inner)])
        leaves = main._leaf_exceptions(eg)
        assert [type(e).__name__ for e in leaves] == ["KafkaError", "OSError", "ValueError"]

    def test_single_exception_returned_as_is(self) -> None:
        err = KafkaError("solo")
        assert main._leaf_exceptions(err) == [err]


class TestReconnectFields:
    """The reconnect log uses the repo-standard scalar error/error_type for the primary leaf,
    and still surfaces sibling/nested causes via error_count + other_error_types."""

    def test_primary_leaf_uses_standard_scalar_fields(self) -> None:
        primary = OSError("connection refused")
        leaves = main._leaf_exceptions(ExceptionGroup("boom", [primary, KafkaError("b")]))
        fields = main._reconnect_fields(leaves)
        assert fields["error"] == str(primary)               # repo-standard scalar, primary = first leaf
        assert fields["error_type"] == "OSError"
        assert fields["error_count"] == 2
        assert fields["other_error_types"] == ["KafkaError"]  # remaining leaf type names

    def test_single_leaf_has_no_other_types(self) -> None:
        err = OSError("solo")
        fields = main._reconnect_fields([err])
        assert fields == {"error": str(err), "error_type": "OSError",
                          "error_count": 1, "other_error_types": []}


class TestIngestStats:
    """Counters behind the periodic ingest summary."""

    def test_snapshot_resets_the_counters(self) -> None:
        s = main.IngestStats()
        s.rows, s.unparseable, s.topics = 5, 2, {"a", "b"}
        assert s.snapshot_and_reset() == (5, 2, 2)
        assert s.snapshot_and_reset() == (0, 0, 0)


@pytest.mark.asyncio
class TestIngestStatsInReadLoop:
    """The read loop feeds the summary counters and flood-proofs the unparseable warning."""

    @pytest.fixture(autouse=True)
    def _passthrough_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main, "Message",
                            lambda type, resource, payload: SimpleNamespace(resource=resource, payload=payload))

    async def test_unparseable_records_warn_once_and_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bad records log one detailed warning per interval; the rest only count toward
        the periodic summary instead of flooding the log."""
        warnings: list[str] = []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append(msg))
        m = main.StreamMapping("pump-1", "temp", _type("number"), None, None)
        stats = main.IngestStats()
        consumer = _FakeConsumer([_record(b"not-a-number", None, "telemetry")] * 3)

        await main._read_loop(consumer, {"telemetry": [m]}, stats)

        assert warnings.count("Skipping unparseable record") == 1
        assert stats.unparseable == 3 and stats.rows == 0

    async def test_published_rows_and_topics_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main.app, "publish", lambda m: _noop())
        m = main.StreamMapping("pump-1", "temp", _type("number"), None, None)
        stats = main.IngestStats()
        consumer = _FakeConsumer([_record(b"1.0", None, "telemetry"), _record(b"2.0", None, "telemetry")])

        await main._read_loop(consumer, {"telemetry": [m]}, stats)

        assert stats.rows == 2 and stats.unparseable == 0 and stats.topics == {"telemetry"}
