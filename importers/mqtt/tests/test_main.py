"""Unit tests for the MQTT connector's pure helpers + control-writeback handling."""
import asyncio
import contextlib
import uuid
from types import SimpleNamespace

import aiomqtt
import pytest
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message.base_messages import StateEnum
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main


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


def _cc(asset: str, stream: str, value: object):
    """Fake ControlChange carrying a datastream resource, an id, and a scalar/object payload."""
    return SimpleNamespace(
        resource=KRNAssetDataStream(asset, stream),
        id=uuid.uuid4(),
        payload=SimpleNamespace(payload=value),
    )


class TestDecodeValue:
    def test_number(self) -> None:
        assert main.decode_value(b"42.5", "number", None) == 42.5

    def test_string(self) -> None:
        assert main.decode_value(b"running", "string", None) == "running"

    def test_object_whole_payload(self) -> None:
        assert main.decode_value(b'{"a": 1}', "object", None) == {"a": 1}

    @pytest.mark.parametrize("raw,expected", [
        (b"true", True), (b"1", True), (b"on", True), (b"false", False), (b"0", False), (b"", False),
    ])
    def test_boolean_parsed_not_truth_tested(self, raw: bytes, expected: bool) -> None:
        assert main.decode_value(raw, "boolean", None) is expected

    def test_payload_field_extraction(self) -> None:
        raw = b'{"temperature": 71.4, "humidity": 38.0}'
        assert main.decode_value(raw, "number", "temperature") == 71.4
        assert main.decode_value(raw, "number", "humidity") == 38.0

    def test_payload_field_nested(self) -> None:
        assert main.decode_value(b'{"readings": {"pressure": 4.2}}', "number", "readings.pressure") == 4.2

    def test_missing_field_raises(self) -> None:
        with pytest.raises(KeyError):
            main.decode_value(b'{"a": 1}', "number", "b")

    def test_bad_number_raises(self) -> None:
        with pytest.raises(ValueError):
            main.decode_value(b"nope", "number", None)

    def test_json_bool_field_not_coerced_to_number(self) -> None:
        """A JSON boolean pulled from a field must not slip through as 1.0/0.0."""
        with pytest.raises(ValueError):
            main.decode_value(b'{"flag": true}', "number", "flag")


class TestCoerce:
    @pytest.mark.parametrize("value", [True, False])
    def test_number_rejects_bool(self, value: bool) -> None:
        """bool is an int subclass, so float(True)==1.0; the number branch must reject it."""
        with pytest.raises(ValueError):
            main.coerce(value, "number")

    def test_number_accepts_numeric(self) -> None:
        assert main.coerce(3, "number") == 3.0 and main.coerce("4.5", "number") == 4.5

    def test_boolean_branch_keeps_bool(self) -> None:
        assert main.coerce(True, "boolean") is True


class TestResolve:
    def test_substitutes_placeholders(self) -> None:
        assert main.resolve("sensors/{asset}/{stream}", "pump-1", "temp") == "sensors/pump-1/temp"

    def test_literal_unchanged(self) -> None:
        assert main.resolve("plant/pump-1/temp", "pump-1", "temp") == "plant/pump-1/temp"


class TestPrimitiveName:
    def test_from_string(self) -> None:
        assert main.primitive_name(_type("number")) == "number"

    def test_from_enum_like(self) -> None:
        assert main.primitive_name(SimpleNamespace(primitive=SimpleNamespace(value="boolean"))) == "boolean"


class TestBuildTopicMap:
    def test_resolves_topic_and_captures_field(self) -> None:
        assets = _assets({"pump-1": {"temp": ("number", {"topic": "sensors/{asset}/t", "payload_field": "v"})}})
        tm = main.build_topic_map(assets)
        (m,) = tm["sensors/pump-1/t"]
        assert m.asset == "pump-1" and m.stream == "temp" and m.payload_field == "v"
        assert main.primitive_name(m.msg_type) == "number"

    def test_skips_stream_without_topic(self) -> None:
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
        assert main.build_topic_map(_assets({"a": {"sp": ("number", {"control_topic": "plant/a/cmd"})}})) == {}
        assert warnings == []


class TestBuildCommandMap:
    def test_resolves_control_topic(self) -> None:
        assets = _assets({"pump-1": {"sp": ("number", {"topic": "x", "control_topic": "plant/{asset}/cmd"})}})
        assert main.build_command_map(assets) == {("pump-1", "sp"): "plant/pump-1/cmd"}

    def test_skips_without_control_topic(self) -> None:
        assert main.build_command_map(_assets({"a": {"s": ("number", {"topic": "x"})}})) == {}


class TestMatchTargets:
    def test_wildcard(self) -> None:
        m = main.StreamMapping("a", "temp", _type("number"), None)
        assert main.match_targets(aiomqtt.Topic("plant/1/temp"), {"plant/+/temp": [m]}) == [m]

    def test_no_match(self) -> None:
        m = main.StreamMapping("a", "temp", _type("number"), None)
        assert main.match_targets(aiomqtt.Topic("other"), {"plant/temp": [m]}) == []


class TestCommandPayload:
    def test_scalar(self) -> None:
        assert main.command_payload(55) == "55" and main.command_payload("open") == "open"

    def test_object(self) -> None:
        assert main.command_payload({"mode": "auto"}) == '{"mode": "auto"}'


@pytest.mark.asyncio
class TestControlWriteback:
    class _FakeClient:
        def __init__(self) -> None:
            self.published: list[tuple] = []

        async def publish(self, topic, payload, qos=0):
            self.published.append((topic, payload, qos))

    class _FailingClient:
        async def publish(self, topic, payload, qos=0):
            raise aiomqtt.MqttError("broker unavailable")

    async def test_enqueue_and_handle_publishes_and_acks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        client = self._FakeClient()
        msg = _cc("pump-1", "setpoint", 55)

        await main.on_control_change(msg)                 # handler just enqueues
        assert main._commands.qsize() == 1
        _enqueued_at, queued = await main._commands.get()  # queue carries (timestamp, command)
        await main._handle_command(client, {("pump-1", "setpoint"): "plant/pump-1/cmd"}, queued)

        assert client.published == [("plant/pump-1/cmd", "55", 1)]   # QoS 1 (broker-acknowledged)
        assert len(acks) == 1 and acks[0].payload.state == StateEnum.processed

    async def test_non_datastream_resource_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        client = self._FakeClient()
        msg = SimpleNamespace(resource=KRNAsset("pump-1"), id=uuid.uuid4(),
                              payload=SimpleNamespace(payload=55))

        await main._handle_command(client, {}, msg)

        assert client.published == []
        assert len(acks) == 1 and acks[0].payload.state == StateEnum.failed

    async def test_unmapped_control_topic_is_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        client = self._FakeClient()
        await main._handle_command(client, {}, _cc("pump-1", "setpoint", 55))
        assert client.published == [] and acks[0].payload.state == StateEnum.failed

    async def test_publish_failure_acks_failed_and_reraises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        with pytest.raises(aiomqtt.MqttError):
            await main._handle_command(self._FailingClient(), {("pump-1", "setpoint"): "plant/pump-1/cmd"},
                                       _cc("pump-1", "setpoint", 55))
        assert acks[0].payload.state == StateEnum.failed

    async def test_handle_command_error_propagates_out_of_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_command_loop only catches CancelledError; an aiomqtt.MqttError raised by
        _handle_command must propagate out so the TaskGroup tears down and main reconnects,
        not be swallowed inside the loop."""
        monkeypatch.setattr(main.app, "publish", lambda m: _noop())
        monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=10))

        async def _boom(client, command_map, msg) -> None:
            raise aiomqtt.MqttError("broker unavailable")

        monkeypatch.setattr(main, "_handle_command", _boom)
        main._commands.put_nowait((main.time.monotonic(), _cc("pump-1", "setpoint", 55)))

        with pytest.raises(aiomqtt.MqttError):
            await main._command_loop(self._FakeClient(), {})

    async def test_full_queue_rejects_with_failed_ack(self, monkeypatch: pytest.MonkeyPatch) -> None:
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=1))
        main._commands.put_nowait((main.time.monotonic(), _cc("pump-1", "setpoint", 1)))   # fill it

        await main.on_control_change(_cc("pump-1", "setpoint", 2))

        assert main._commands.qsize() == 1                             # rejected, not enqueued
        assert acks[0].payload.state == StateEnum.failed

    async def test_cancellation_mid_command_acks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Teardown between dequeue and completion must still leave a terminal (failed) ack
        for the in-flight command, so the platform never waits on it forever."""
        acks: list = []
        monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
        monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=10))
        entered = asyncio.Event()

        async def _stuck_handle(client, command_map, msg) -> None:
            entered.set()
            await asyncio.Event().wait()                               # block until cancelled

        monkeypatch.setattr(main, "_handle_command", _stuck_handle)
        main._commands.put_nowait((main.time.monotonic(), _cc("pump-1", "setpoint", 55)))

        task = asyncio.create_task(main._command_loop(self._FakeClient(), {}))
        await entered.wait()                                           # command is in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(acks) == 1 and acks[0].payload.state == StateEnum.failed
        assert "shutting down" in acks[0].payload.message


@pytest.mark.asyncio
class TestConsumeMappingLogs:
    """Gated logging in _consume: warn only when nothing at all is mapped;
    a control_topic-only deployment is a valid writeback-only setup."""

    class _FakeMqttClient:
        """Stands in for aiomqtt.Client: an async context manager that records subscriptions."""

        def __init__(self, **kwargs: object) -> None:
            self.subscriptions: list[tuple[str, int]] = []

        async def __aenter__(self) -> "TestConsumeMappingLogs._FakeMqttClient":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def subscribe(self, topic: str, qos: int = 0) -> None:
            self.subscriptions.append((topic, qos))

    async def _run_consume(self, monkeypatch: pytest.MonkeyPatch, assets: dict) -> tuple[list[str], list[str]]:
        """Run _consume with fakes; return the (warning, info) log messages emitted."""
        warnings: list[str] = []
        infos: list[str] = []
        _patch_consume_collaborators(monkeypatch, assets, self._FakeMqttClient)
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append(msg))
        monkeypatch.setattr(main.logger, "info", lambda msg, **kw: infos.append(msg))
        await main._consume(main.Settings())
        return warnings, infos

    async def test_writeback_only_logs_info_not_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _assets({"pump-1": {"setpoint": ("number", {"control_topic": "plant/pump-1/cmd"})}})
        warnings, infos = await self._run_consume(monkeypatch, assets)
        assert warnings == []
        assert any("writeback-only" in m for m in infos)

    async def test_nothing_mapped_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        warnings, infos = await self._run_consume(monkeypatch, {})
        assert any("No streams mapped" in m for m in warnings)
        assert not any("writeback-only" in m for m in infos)

    async def test_inbound_topics_mapped_logs_neither(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _assets({"pump-1": {"temp": ("number", {"topic": "plant/pump-1/temp"})}})
        warnings, infos = await self._run_consume(monkeypatch, assets)
        assert warnings == []
        assert not any("writeback-only" in m for m in infos)


def _patch_consume_collaborators(monkeypatch: pytest.MonkeyPatch, assets: dict, client_cls: type) -> None:
    """Patch the collaborators _consume reaches for: the MQTT client class, app.assets, and the
    three long-lived loops (stubbed to no-ops so _consume returns instead of blocking)."""
    monkeypatch.setattr(main.aiomqtt, "Client", client_cls)
    monkeypatch.setattr(main, "app", SimpleNamespace(assets=assets))
    monkeypatch.setattr(main, "_read_loop", lambda client, tm, stats=None: _noop())
    monkeypatch.setattr(main, "_command_loop", lambda client, cm: _noop())
    monkeypatch.setattr(main, "_report_loop", lambda stats: _noop())


def _capturing_client_cls(captured: list) -> type:
    """A _FakeMqttClient subclass that records each constructed instance into `captured`."""

    class _CapturingClient(TestConsumeMappingLogs._FakeMqttClient):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            captured.append(self)

    return _CapturingClient


@pytest.mark.asyncio
class TestSubscribeQoS:
    """The configured subscribe QoS reaches client.subscribe for every inbound topic."""

    @pytest.mark.parametrize("settings,expected_qos", [
        (main.Settings(qos=2), 2),
        (main.Settings(), 0),
    ])
    async def test_configured_qos_reaches_subscribe(
        self, monkeypatch: pytest.MonkeyPatch, settings: "main.Settings", expected_qos: int
    ) -> None:
        captured: list = []
        assets = _assets({"pump-1": {"temp": ("number", {"topic": "plant/pump-1/temp"})}})
        _patch_consume_collaborators(monkeypatch, assets, _capturing_client_cls(captured))

        await main._consume(settings)

        assert captured[0].subscriptions == [("plant/pump-1/temp", expected_qos)]


@pytest.mark.asyncio
class TestReadLoopEndTriggersReconnect:
    """A read stream that ends without an MqttError must still raise so the outer loop reconnects."""

    async def test_normal_stream_end_raises(self) -> None:
        client = SimpleNamespace(messages=_FakeMessages([]))
        with pytest.raises(aiomqtt.MqttError):
            await main._read_loop(client, {}, main.IngestStats())


async def _drain_one_command(monkeypatch: pytest.MonkeyPatch, timestamp: float) -> tuple[list, list]:
    """Enqueue a single command stamped `timestamp`, let _command_loop drain it once, then cancel,
    suppress the CancelledError, and await. Returns the captured (published, acks) lists from the
    patched _handle_command and app.publish so each caller can make its own assertions."""
    acks: list = []
    published: list = []
    monkeypatch.setattr(main.app, "publish", lambda m: acks.append(m) or _noop())
    monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=10))

    async def _record_handle(client, command_map, msg) -> None:
        published.append(msg)

    monkeypatch.setattr(main, "_handle_command", _record_handle)
    main._commands.put_nowait((timestamp, _cc("pump-1", "setpoint", 55)))

    task = asyncio.create_task(main._command_loop(SimpleNamespace(), {}))
    await asyncio.sleep(0)                          # let the loop drain the item
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return published, acks


@pytest.mark.asyncio
class TestStaleCommandDrop:
    """Commands enqueued while disconnected must be dropped (acked failed), not replayed."""

    async def test_stale_command_dropped_and_acked_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stale_ts = main.time.monotonic() - main._COMMAND_MAX_AGE - 5
        published, acks = await _drain_one_command(monkeypatch, stale_ts)

        assert published == []                          # never forwarded to the broker
        assert len(acks) == 1 and acks[0].payload.state == StateEnum.failed
        assert "stale" in acks[0].payload.message

    async def test_stale_ack_still_emitted_on_teardown_cancel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If teardown cancels the stale-drop ack mid-publish, a terminal failed ack is still
        best-effort emitted, matching the protection the in-flight command path gets."""
        acks: list = []
        completed: list = []
        entered = asyncio.Event()
        calls = 0

        def _publish(m):
            nonlocal calls
            calls += 1
            acks.append(m)
            block_this = calls == 1

            async def _pub() -> None:
                if block_this:
                    entered.set()
                    await asyncio.Event().wait()        # stale ack blocks until cancelled
                completed.append(m)

            return _pub()

        monkeypatch.setattr(main.app, "publish", _publish)
        monkeypatch.setattr(main, "_commands", asyncio.Queue(maxsize=10))
        stale_ts = main.time.monotonic() - main._COMMAND_MAX_AGE - 5
        main._commands.put_nowait((stale_ts, _cc("pump-1", "setpoint", 55)))

        task = asyncio.create_task(main._command_loop(SimpleNamespace(), {}))
        await entered.wait()                            # stale-drop ack is awaiting (mid-publish)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The blocked stale ack never completed, but teardown emitted a terminal failed ack.
        assert completed and completed[-1].payload.state == StateEnum.failed
        assert "shutting down" in completed[-1].payload.message

    async def test_fresh_command_still_handled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        published, _acks = await _drain_one_command(monkeypatch, main.time.monotonic())
        assert len(published) == 1


class TestFlattenExceptions:
    """The reconnect log must surface every failure, including nested groups."""

    def test_flattens_nested_groups(self) -> None:
        inner = ExceptionGroup("inner", [OSError("dns"), aiomqtt.MqttError("timeout")])
        outer = ExceptionGroup("outer", [inner, aiomqtt.MqttError("reset")])
        leaves = main._flatten_exceptions(outer)
        messages = {str(e) for e in leaves}
        assert messages == {"dns", "timeout", "reset"}

    def test_single_exception_returns_itself(self) -> None:
        err = aiomqtt.MqttError("boom")
        assert main._flatten_exceptions(err) == [err]


@pytest.mark.asyncio
class TestReconnectLogShape:
    """The reconnect warning carries the repo-standard scalar fields for the primary leaf,
    plus a count and the remaining leaf types so sibling/nested causes still surface."""

    async def _run_one_reconnect(self, monkeypatch: pytest.MonkeyPatch, error: BaseException) -> dict:
        logs: list[tuple[str, dict]] = []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: logs.append((msg, kw)))

        async def _connect() -> None:
            return None

        monkeypatch.setattr(main, "app", SimpleNamespace(connect=_connect, app_configuration={}))

        async def _consume(settings: object) -> None:
            raise error

        monkeypatch.setattr(main, "_consume", _consume)

        class _Stop(Exception):
            pass

        async def _sleep(_seconds: float) -> None:
            raise _Stop                                 # break out of the reconnect loop

        monkeypatch.setattr(main.asyncio, "sleep", _sleep)

        with pytest.raises(_Stop):
            await main.main()

        assert len(logs) == 1
        msg, kw = logs[0]
        assert msg == "MQTT connection lost; reconnecting"
        return kw

    async def test_primary_leaf_uses_standard_scalar_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = ExceptionGroup("boom", [aiomqtt.MqttError("broker gone"), OSError("dns down")])
        kw = await self._run_one_reconnect(monkeypatch, group)
        assert kw["error"] == "broker gone"
        assert kw["error_type"] == "MqttError"
        assert kw["error_count"] == 2
        assert kw["other_error_types"] == ["OSError"]

    async def test_single_leaf_has_empty_supplementary_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = ExceptionGroup("boom", [OSError("connection refused")])
        kw = await self._run_one_reconnect(monkeypatch, group)
        assert kw["error"] == "connection refused"
        assert kw["error_type"] == "OSError"
        assert kw["error_count"] == 1
        assert kw["other_error_types"] == []


class _FakeMessages:
    """Async-iterable over canned aiomqtt-style messages (topic is an aiomqtt.Topic)."""
    def __init__(self, messages: list) -> None:
        self._messages = messages

    def __aiter__(self) -> "_FakeMessages":
        self._it = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _msg(topic: str, payload: bytes) -> SimpleNamespace:
    return SimpleNamespace(topic=aiomqtt.Topic(topic), payload=payload)


@pytest.mark.asyncio
class TestPublishHarness:
    """End-to-end via the SDK harness: a message flows through the real Message publish path
    (real KMessageType from app.assets), and we assert on the captured Kelvin output."""

    @staticmethod
    def _manifest(streams: list[tuple[str, str, dict]]):
        mb = ManifestBuilder.from_app_yaml().add_asset("pump-1")
        for name, dtype, cfg in streams:
            mb = mb.add_input(name, dtype, configuration=cfg)
        return mb.set_configuration({"mqtt": {"host": "test.local"}}).build()

    async def test_each_type_publishes_with_native_value(self) -> None:
        manifest = self._manifest([
            ("temperature", "number", {"topic": "t/num"}),
            ("status", "string", {"topic": "t/str"}),
            ("enabled", "boolean", {"topic": "t/bool"}),
            ("readings", "object", {"topic": "t/obj"}),
        ])
        async with KelvinAppTest(main.app, manifest=manifest) as harness:
            topic_map = main.build_topic_map(main.app.assets)
            client = SimpleNamespace(messages=_FakeMessages([
                _msg("t/num", b"42.5"),
                _msg("t/str", b"running"),
                _msg("t/bool", b"true"),
                _msg("t/obj", b'{"p": 1}'),
            ]))
            with pytest.raises(aiomqtt.MqttError):   # a stream that ends triggers reconnect
                await main._read_loop(client, topic_map)
            out = {o.resource.data_stream: o.payload for o in harness.outputs}

        assert out["temperature"] == 42.5
        assert out["status"] == "running"
        assert out["enabled"] is True
        assert out["readings"] == {"p": 1}

    async def test_wildcard_topic_and_payload_field(self) -> None:
        manifest = self._manifest([("pressure", "number",
                                    {"topic": "plant/+/readings", "payload_field": "readings.pressure"})])
        async with KelvinAppTest(main.app, manifest=manifest) as harness:
            topic_map = main.build_topic_map(main.app.assets)
            client = SimpleNamespace(messages=_FakeMessages([
                _msg("plant/pump-1/readings", b'{"readings": {"pressure": 4.2}}'),
            ]))
            with pytest.raises(aiomqtt.MqttError):   # a stream that ends triggers reconnect
                await main._read_loop(client, topic_map)
            out = harness.outputs

        assert len(out) == 1 and out[0].payload == 4.2 and out[0].resource.data_stream == "pressure"


async def _noop() -> None:
    return None


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

    async def test_unparseable_payloads_warn_once_and_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bad payloads log one detailed warning per interval; the rest only count toward
        the periodic summary instead of flooding the log."""
        warnings: list[str] = []
        monkeypatch.setattr(main.logger, "warning", lambda msg, **kw: warnings.append(msg))
        m = main.StreamMapping("pump-1", "temp", _type("number"), None)
        stats = main.IngestStats()
        client = SimpleNamespace(messages=_FakeMessages([_msg("t/num", b"not-a-number")] * 3))

        with pytest.raises(aiomqtt.MqttError):
            await main._read_loop(client, {"t/num": [m]}, stats)

        assert warnings.count("Skipping unparseable payload") == 1
        assert stats.unparseable == 3 and stats.rows == 0

    async def test_published_rows_and_topics_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main.app, "publish", lambda m: _noop())
        m = main.StreamMapping("pump-1", "temp", _type("number"), None)
        stats = main.IngestStats()
        client = SimpleNamespace(messages=_FakeMessages([_msg("t/num", b"1.0"), _msg("t/num", b"2.0")]))

        with pytest.raises(aiomqtt.MqttError):
            await main._read_loop(client, {"t/num": [m]}, stats)

        assert stats.rows == 2 and stats.unparseable == 0 and stats.topics == {"t/num"}
