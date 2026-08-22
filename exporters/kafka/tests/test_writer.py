"""Unit tests for KafkaWriter's record building, producer lifecycle, and client kwargs (no broker)."""
import asyncio
import json
import ssl
from datetime import datetime
from pathlib import Path

import pytest
from aiokafka.errors import (
    AuthenticationFailedError,
    MessageSizeTooLargeError,
    UnknownTopicOrPartitionError,
)

import writer as writer_mod
from writer import KafkaWriter, build_ssl_context, client_kwargs

from settings import Settings

TOPICS = {("a", "d"): "telemetry.a"}
SASL = {"mechanism": "PLAIN", "username": "u", "password": "p"}


def _cfg(**kafka):
    kafka.setdefault("bootstrap_servers", "broker:9093")
    return Settings(kafka=kafka).kafka


def _row(payload, asset: str = "a", datastream: str = "d"):
    return {"timestamp": datetime(2026, 1, 1, 12, 0, 0), "asset": asset, "datastream": datastream,
            "payload": payload}


class _FakeProducer:
    """Records sends and start/stop calls; optionally blows up on start, send, or partitions_for."""

    def __init__(self, fail_start: Exception | None = None, fail_send: Exception | None = None,
                 fail_partitions: Exception | None = None, max_size: int | None = None) -> None:
        self.sent: list[tuple] = []
        self.started = 0
        self.stopped = 0
        self.partition_calls: list[str] = []
        # fail_start is one-shot: enough to model "unreachable at setup, back by the first tick".
        self._fail_start = fail_start
        self._fail_send = fail_send
        self._fail_partitions = fail_partitions
        # max_size models max_request_size: send() raises MessageSizeTooLargeError synchronously
        # for an oversized value, exactly like aiokafka's eager serialize check (a poison record).
        self._max_size = max_size

    async def start(self) -> None:
        if self._fail_start is not None:
            exc, self._fail_start = self._fail_start, None
            raise exc
        self.started += 1

    async def send(self, topic, value=None, key=None):
        if self._max_size is not None and value is not None and len(value) > self._max_size:
            raise MessageSizeTooLargeError(f"{len(value)} bytes > max_request_size {self._max_size}")
        self.sent.append((topic, value, key))
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        if self._fail_send is not None:
            fut.set_exception(self._fail_send)
        else:
            fut.set_result(None)
        return fut

    async def partitions_for(self, topic: str):
        self.partition_calls.append(topic)
        if self._fail_partitions is not None:
            raise self._fail_partitions
        return {0}

    async def stop(self) -> None:
        self.stopped += 1


class _FakeStore:
    """Minimal Store stand-in: read() returns a Records-like batch once, then empties."""

    def __init__(self, rows: list[dict]) -> None:
        from store import Records
        self._records = Records(rows, len(rows) if rows else None, len(rows), 0)

    async def read(self, limit: int):
        return self._records


def _patch_producer(monkeypatch: pytest.MonkeyPatch, producer: _FakeProducer) -> dict:
    """Replace writer.AIOKafkaProducer so _ensure_producer builds our fake; capture its kwargs."""
    holder: dict = {}

    def factory(**kwargs):
        holder["kwargs"] = kwargs
        return producer

    monkeypatch.setattr(writer_mod, "AIOKafkaProducer", factory)
    return holder


class TestBuildRecord:
    """Record building: JSON value bytes + partition key."""

    def test_serializes_timestamp_and_keeps_native_payload(self) -> None:
        """build_record renders the timestamp as ISO-8601 and passes scalar payloads through."""
        rec = json.loads(KafkaWriter.build_record(_row(42.5)))
        assert rec == {"timestamp": "2026-01-01T12:00:00+00:00", "asset": "a",
                       "datastream": "d", "payload": 42.5}

    def test_timestamp_carries_explicit_utc_marker(self) -> None:
        """The Store keeps naive UTC; the exported string must carry an explicit UTC marker so a
        consumer never reads it as local wall-clock time."""
        ts = json.loads(KafkaWriter.build_record(_row(1.0)))["timestamp"]
        assert ts.endswith("+00:00")
        assert datetime.fromisoformat(ts).utcoffset().total_seconds() == 0

    @pytest.mark.parametrize("payload", ["running", True])
    def test_passes_string_and_bool_through(self, payload) -> None:
        assert json.loads(KafkaWriter.build_record(_row(payload)))["payload"] == payload

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_coerces_non_finite_payload_to_none(self, bad: float) -> None:
        """NaN/Inf would make the message's JSON invalid; they become None."""
        assert json.loads(KafkaWriter.build_record(_row(bad)))["payload"] is None

    def test_key_is_asset_slash_datastream(self) -> None:
        """The key pins each stream to one partition (per-stream ordering)."""
        assert KafkaWriter.build_key(_row(1.0)) == b"a/d"


@pytest.mark.asyncio
class TestProducerLifecycle:
    """Eager start at setup, send+gather per batch, failure-stops-producer, teardown."""

    async def test_batch_sends_each_row_with_topic_and_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        producer = _FakeProducer()
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        r = await w.write_batch(_FakeStore([_row(1.0), _row(2.0)]), 100)

        assert r.n_rows == 2 and len(producer.sent) == 2
        topic, value, key = producer.sent[0]
        assert topic == "telemetry.a" and key == b"a/d"
        assert json.loads(value)["payload"] == 1.0

    async def test_unmapped_row_is_skipped_not_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A row whose stream lost its mapping (redeploy) is discarded with a warning, not sent."""
        producer = _FakeProducer()
        _patch_producer(monkeypatch, producer)
        warnings: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "warning", lambda msg, **kw: warnings.append(msg))
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        r = await w.write_batch(_FakeStore([_row(1.0), _row(2.0, asset="ghost")]), 100)

        assert r.n_rows == 2 and len(producer.sent) == 1     # ghost row skipped
        assert any("no longer mapped" in msg for msg in warnings)

    async def test_empty_batch_sends_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        producer = _FakeProducer()
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        r = await w.write_batch(_FakeStore([]), 100)

        assert r.n_rows == 0 and producer.sent == []

    async def test_failed_send_stops_producer_and_reraises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A delivery failure stops the producer (so the next attempt restarts) and re-raises."""
        producer = _FakeProducer(fail_send=RuntimeError("delivery failed"))
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        with pytest.raises(RuntimeError, match="delivery failed"):
            await w.write_batch(_FakeStore([_row(1.0)]), 100)

        assert producer.stopped == 1 and w._producer is None

    async def test_producer_is_reused_across_batches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        producer = _FakeProducer()
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        await w.write_batch(_FakeStore([_row(1.0)]), 100)
        await w.write_batch(_FakeStore([_row(2.0)]), 100)

        assert producer.started == 1     # started once at setup, reused

    async def test_teardown_stops_the_producer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        producer = _FakeProducer()
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        await w.teardown()

        assert producer.stopped == 1 and w._producer is None

    async def test_idempotent_acks_all_producer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The delivery contract is hardwired: acks=all + idempotence, not configurable."""
        holder = _patch_producer(monkeypatch, _FakeProducer())
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        assert holder["kwargs"]["acks"] == "all"
        assert holder["kwargs"]["enable_idempotence"] is True


@pytest.mark.asyncio
class TestPoisonRecord:
    """A permanently un-producible record is dead-lettered (skipped with a warning) so it can't
    wedge the FIFO buffer; transient failures still fail the whole batch for retry."""

    async def test_poison_record_skipped_and_following_records_deliver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An oversized payload raises MessageSizeTooLargeError on send; it's dropped with a
        warning that names the record, the good rows around it still deliver, and write_batch
        returns normally (cursor intact) so the drain drops the poison and the buffer advances."""
        producer = _FakeProducer(max_size=200)
        _patch_producer(monkeypatch, producer)
        warnings: list[tuple] = []
        monkeypatch.setattr(writer_mod.logger, "warning", lambda msg, **kw: warnings.append((msg, kw)))
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        big = "x" * 500        # serialized value exceeds max_size -> poison
        r = await w.write_batch(_FakeStore([_row(1.0), _row(big), _row(2.0)]), 100)

        # good rows delivered, poison skipped
        assert [json.loads(v)["payload"] for _, v, _ in producer.sent] == [1.0, 2.0]
        # cursor still covers the whole batch: the drain drops the poison too (buffer advances)
        assert r.n_rows == 3 and r.cursor is not None
        assert w._producer is producer         # NOT closed: a per-record drop keeps the pipeline up
        poison = [kw for msg, kw in warnings if "dead-letter" in msg.lower()]
        assert poison and poison[0]["asset"] == "a" and poison[0]["datastream"] == "d"
        assert poison[0]["error_type"] == "MessageSizeTooLargeError"

    async def test_poison_via_delivery_future_is_dead_lettered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A record the broker rejects permanently (poison error on its delivery future) is
        dead-lettered too, not retried forever."""
        producer = _FakeProducer(fail_send=MessageSizeTooLargeError("broker rejected: too large"))
        _patch_producer(monkeypatch, producer)
        warnings: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "warning", lambda msg, **kw: warnings.append(msg))
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        r = await w.write_batch(_FakeStore([_row(1.0)]), 100)

        assert r.n_rows == 1 and r.cursor is not None    # returns for drop; not wedged
        assert w._producer is producer                   # producer kept open
        assert any("dead-letter" in m.lower() for m in warnings)

    async def test_transient_error_fails_whole_batch_for_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient/whole-broker error is NOT skipped: write_batch stops the producer and
        re-raises so the whole batch stays buffered and retries next interval."""
        producer = _FakeProducer(fail_send=ConnectionError("brokers down"))
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        with pytest.raises(ConnectionError):
            await w.write_batch(_FakeStore([_row(1.0), _row(2.0)]), 100)

        assert producer.stopped == 1 and w._producer is None

    @pytest.mark.parametrize("poison_idx", [0, 1], ids=["poison-first", "poison-middle"])
    async def test_mixed_poison_and_transient_reraises(
        self, monkeypatch: pytest.MonkeyPatch, poison_idx: int
    ) -> None:
        """A batch holding BOTH a poison record and a transient failure must re-raise: the poison
        is dead-lettered, but the transient wins and the whole batch stays buffered for retry. The
        outcome is the same wherever the poison sits, so the re-raise is driven by the transient
        error, not by ordering.

        The oversized row raises MessageSizeTooLargeError synchronously on send (poison, skipped
        per-row); the good rows' delivery futures raise a transient ConnectionError. write_batch
        never returns, so the drain never calls store.drop / advances the cursor: no data loss."""
        producer = _FakeProducer(max_size=200, fail_send=ConnectionError("brokers down"))
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)
        await w.setup()

        big = "x" * 500        # serialized value exceeds max_size -> poison
        rows = [_row(1.0), _row(2.0)]
        rows.insert(poison_idx, _row(big))
        with pytest.raises(ConnectionError):
            await w.write_batch(_FakeStore(rows), 100)

        # transient outcome, not the poison: producer dropped so the next attempt restarts clean
        assert producer.stopped == 1 and w._producer is None


@pytest.mark.asyncio
class TestSetupClassification:
    """setup() policy: deterministic config errors crash the deploy; anything else warns and buffers."""

    async def test_success_checks_topics_and_logs_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A clean setup starts the producer, probes each unique topic, and logs 'ready'."""
        producer = _FakeProducer()
        _patch_producer(monkeypatch, producer)
        infos: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "info", lambda msg, **kw: infos.append(msg))
        w = KafkaWriter(_cfg(), {("a", "d"): "t1", ("a", "e"): "t1", ("b", "d"): "t2"})

        await w.setup()

        assert producer.partition_calls == ["t1", "t2"]      # unique topics, sorted
        assert "Kafka writer ready" in infos

    async def test_auth_failure_at_start_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bad credentials fail the deployment visibly (and the half-started producer is stopped)."""
        producer = _FakeProducer(fail_start=AuthenticationFailedError("bad credentials"))
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)

        with pytest.raises(AuthenticationFailedError):
            await w.setup()

        assert producer.stopped == 1 and w._producer is None

    async def test_missing_topic_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A nonexistent/forbidden topic is a config error: crash the deployment."""
        producer = _FakeProducer(fail_partitions=UnknownTopicOrPartitionError())
        _patch_producer(monkeypatch, producer)
        w = KafkaWriter(_cfg(), TOPICS)

        with pytest.raises(UnknownTopicOrPartitionError):
            await w.setup()

    async def test_transient_failure_warns_and_first_batch_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A transient/unknown failure warns (no 'ready' log), doesn't raise, and leaves the
        writer usable: the first batch restarts the producer and sends."""
        producer = _FakeProducer(fail_start=ConnectionError("brokers down"))
        _patch_producer(monkeypatch, producer)
        warnings: list[str] = []
        infos: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "warning", lambda msg, **kw: warnings.append(msg))
        monkeypatch.setattr(writer_mod.logger, "info", lambda msg, **kw: infos.append(msg))
        w = KafkaWriter(_cfg(), TOPICS)

        await w.setup()                                # logs a warning, does not raise

        assert warnings == ["Kafka unreachable at setup; buffering and retrying"]
        assert "Kafka writer ready" not in infos and w._producer is None

        r = await w.write_batch(_FakeStore([_row(1.0)]), 100)

        assert r.n_rows == 1 and len(producer.sent) == 1
        assert producer.started == 1                   # failed setup start + first-batch restart


class TestClientKwargs:
    """client_kwargs: protocol/SASL/TLS wiring shared with the Kafka importer."""

    def test_plaintext_has_no_ssl_or_sasl(self) -> None:
        kw = client_kwargs(_cfg())
        assert kw["security_protocol"] == "PLAINTEXT" and kw["client_id"] == "kelvin-kafka-exporter"
        assert "ssl_context" not in kw and "sasl_mechanism" not in kw

    def test_sasl_ssl_carries_credentials_and_context(self) -> None:
        kw = client_kwargs(_cfg(security={"protocol": "SASL_SSL", "sasl": SASL}))
        assert kw["security_protocol"] == "SASL_SSL"
        assert kw["sasl_mechanism"] == "PLAIN" and kw["sasl_plain_username"] == "u"
        assert kw["sasl_plain_password"] == "p"                       # SecretStr unwrapped for aiokafka
        assert isinstance(kw["ssl_context"], ssl.SSLContext)

    def test_sasl_plaintext_has_credentials_but_no_context(self) -> None:
        kw = client_kwargs(_cfg(security={"protocol": "SASL_PLAINTEXT", "sasl": SASL}))
        assert kw["sasl_mechanism"] == "PLAIN" and "ssl_context" not in kw


class TestBuildSslContext:
    """SSL context construction from config-held PEM material."""

    def test_default_context_without_material(self) -> None:
        tls = _cfg(security={"protocol": "SSL"}).security.tls
        ctx = build_ssl_context(tls)
        assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED

    def test_mtls_material_flows_through_temp_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cert/key PEM content is written to temp files for load_cert_chain, then deleted."""
        loaded: dict = {}

        class _Ctx:
            def load_cert_chain(self, certfile: str, keyfile: str) -> None:
                loaded["cert"] = Path(certfile).read_text()
                loaded["key"] = Path(keyfile).read_text()
                loaded["paths"] = (certfile, keyfile)

        monkeypatch.setattr(writer_mod.ssl, "create_default_context", lambda cadata=None: _Ctx())
        tls = _cfg(security={"protocol": "SSL",
                             "tls": {"client_cert": "CERT-PEM", "client_key": "KEY-PEM"}}).security.tls

        build_ssl_context(tls)

        assert loaded["cert"] == "CERT-PEM" and loaded["key"] == "KEY-PEM"
        for path in loaded["paths"]:                      # PEM material must not linger on disk
            assert not Path(path).exists()
