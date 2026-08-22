"""Unit tests for ZerobusWriter's record building and stream lifecycle (no cloud)."""
from datetime import datetime

import pytest

# conftest.py stubs the `zerobus` package in sys.modules when the real wheel isn't installed,
# so writer imports cleanly here; the SDK symbols are monkeypatched with async fakes per test.
import writer as writer_mod
from writer import ZerobusWriter

from settings import Settings


def _cfg():
    """A minimal valid Databricks config (oauth auth)."""
    return Settings(databricks={"server_hostname": "h", "zerobus_endpoint": "ep", "delta_table": "c.s.t",
                                "auth": {"client_id": "cid", "client_secret": "csec"}}).databricks


def _row(payload):
    return {"timestamp": datetime(2026, 1, 1, 12, 0, 0), "asset": "a", "datastream": "d", "payload": payload}


class _FakeStream:
    """Records ingested records and flush/wait/close calls; optionally blows up on flush or ack.

    ``waited`` captures every offset passed to wait_for_offset, so a test can prove the writer
    blocks on the server ACK before returning (the drain trims the buffer only after that).
    """

    def __init__(self, fail_flush: bool = False, fail_ack: bool = False) -> None:
        self.ingested: list[dict] = []
        self.flushed = 0
        self.closed = 0
        self.waited: list[int] = []
        self._fail_flush = fail_flush
        self._fail_ack = fail_ack

    async def ingest_records_offset(self, records: list[dict]) -> int:
        self.ingested.extend(records)
        return len(self.ingested)          # final offset = cumulative record count

    async def flush(self) -> None:
        if self._fail_flush:
            raise RuntimeError("flush failed")
        self.flushed += 1

    async def wait_for_offset(self, offset: int) -> None:
        if self._fail_ack:
            raise RuntimeError("ack failed")
        self.waited.append(offset)

    async def close(self) -> None:
        self.closed += 1


class _FakeSdk:
    """Captures the create_stream credentials and hands back a (single) fake stream.

    `fail_create` is a one-shot: the first create_stream raises it, later calls succeed;
    enough to model "unreachable at setup, back by the first drain tick".
    """

    def __init__(self, endpoint: str, workspace_url: str, stream: "_FakeStream",
                 fail_create: Exception | None = None) -> None:
        self.endpoint, self.workspace_url = endpoint, workspace_url
        self._stream = stream
        self._fail_create = fail_create
        self.create_calls: list[tuple] = []

    async def create_stream(self, client_id, client_secret, table_props, options):
        self.create_calls.append((client_id, client_secret))
        if self._fail_create is not None:
            exc, self._fail_create = self._fail_create, None
            raise exc
        return self._stream


def _patch_sdk(monkeypatch: pytest.MonkeyPatch, stream: _FakeStream,
               fail_create: Exception | None = None) -> dict:
    """Replace writer.ZerobusSdk so setup() builds our fake; return the captured sdk holder.

    Also replace the shared types (RecordType/StreamConfigurationOptions/TableProperties) with
    lightweight fakes so the record/stream construction is independent of the real SDK.
    """
    holder: dict = {}

    def factory(endpoint, workspace_url):
        sdk = _FakeSdk(endpoint, workspace_url, stream, fail_create)
        holder["sdk"] = sdk
        return sdk

    monkeypatch.setattr(writer_mod, "ZerobusSdk", factory)
    monkeypatch.setattr(writer_mod, "StreamConfigurationOptions", lambda **kw: kw)
    monkeypatch.setattr(writer_mod, "TableProperties", lambda table: table)

    class _RecordType:
        JSON = "JSON"

    monkeypatch.setattr(writer_mod, "RecordType", _RecordType)
    return holder


class TestBuildRecord:
    """Record-building helper: JSON-serializable values for ingestion."""

    def test_serializes_timestamp_with_utc_marker_and_keeps_native_payload(self) -> None:
        """build_record renders the naive-UTC timestamp as ISO-8601 with an explicit +00:00
        UTC marker and passes scalar payloads through."""
        rec = ZerobusWriter.build_record(_row(42.5))
        assert rec == {"timestamp": "2026-01-01T12:00:00+00:00", "asset": "a",
                       "datastream": "d", "payload": 42.5}

    @pytest.mark.parametrize("payload", ["running", True])
    def test_passes_string_and_bool_through(self, payload) -> None:
        """String and boolean payloads survive unchanged."""
        assert ZerobusWriter.build_record(_row(payload))["payload"] == payload

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_coerces_non_finite_payload_to_none(self, bad: float) -> None:
        """NaN/Inf would make the record's JSON invalid; they become None."""
        assert ZerobusWriter.build_record(_row(bad))["payload"] is None


@pytest.mark.asyncio
class TestStreamLifecycle:
    """Eager stream open at setup, ingest+flush, failure-closes-stream, teardown."""

    class _FakeStore:
        """Minimal Store stand-in: read() returns a Records-like batch once, then empties."""

        def __init__(self, rows: list[dict]) -> None:
            from store import Records
            self._records = Records(rows, len(rows) if rows else None, len(rows), 0)

        async def read(self, limit: int):
            return self._records

    async def test_batch_ingests_flushes_waits_for_ack_and_unwraps_credentials(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A batch reuses the setup-opened stream, ingests each record, flushes once, waits for
        the server to acknowledge the batch's final offset, with creds unwrapped."""
        stream = _FakeStream()
        holder = _patch_sdk(monkeypatch, stream)
        w = ZerobusWriter(_cfg())
        await w.setup()
        store = self._FakeStore([_row(1.0), _row(2.0)])

        r = await w.write_batch(store, 100)

        assert r.n_rows == 2
        assert len(stream.ingested) == 2 and stream.flushed == 1
        assert stream.ingested[0]["payload"] == 1.0
        assert stream.waited == [2]          # awaited the final offset (2 records) before returning
        assert holder["sdk"].create_calls == [("cid", "csec")]   # SecretStr unwrapped for the SDK

    @pytest.mark.parametrize("kwargs, match", [
        ({"fail_flush": True}, "flush failed"),
        ({"fail_ack": True}, "ack failed"),
    ])
    async def test_failed_write_closes_stream_and_reraises(
            self, monkeypatch: pytest.MonkeyPatch, kwargs: dict, match: str) -> None:
        """A flush or ack failure drops the stream (so the next attempt reopens) and re-raises;
        with the buffer left untrimmed."""
        stream = _FakeStream(**kwargs)
        _patch_sdk(monkeypatch, stream)
        w = ZerobusWriter(_cfg())
        await w.setup()

        with pytest.raises(RuntimeError, match=match):
            await w.write_batch(self._FakeStore([_row(1.0)]), 100)

        assert stream.closed == 1 and w._stream is None

    async def test_empty_batch_ingests_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty buffer (cursor None) ingests nothing; only setup's eager open touched the SDK."""
        stream = _FakeStream()
        holder = _patch_sdk(monkeypatch, stream)
        w = ZerobusWriter(_cfg())
        await w.setup()

        r = await w.write_batch(self._FakeStore([]), 100)

        assert r.n_rows == 0 and stream.ingested == [] and stream.waited == []
        assert len(holder["sdk"].create_calls) == 1     # setup's open, nothing more

    async def test_stream_is_reused_across_batches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """setup opens the stream once; successive batches reuse it (one create_stream total)."""
        stream = _FakeStream()
        holder = _patch_sdk(monkeypatch, stream)
        w = ZerobusWriter(_cfg())
        await w.setup()

        await w.write_batch(self._FakeStore([_row(1.0)]), 100)
        await w.write_batch(self._FakeStore([_row(2.0)]), 100)

        assert len(holder["sdk"].create_calls) == 1   # opened once, reused

    async def test_teardown_closes_the_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """teardown closes an open stream and clears it."""
        stream = _FakeStream()
        _patch_sdk(monkeypatch, stream)
        w = ZerobusWriter(_cfg())
        await w.setup()
        await w.write_batch(self._FakeStore([_row(1.0)]), 100)

        await w.teardown()

        assert stream.closed == 1 and w._stream is None


@pytest.mark.asyncio
class TestSetupClassification:
    """setup() policy: NonRetriable config errors crash the deploy; anything else warns and buffers."""

    async def test_success_opens_stream_and_logs_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A clean setup opens the stream eagerly and only then logs the 'ready' line."""
        stream = _FakeStream()
        holder = _patch_sdk(monkeypatch, stream)
        infos: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "info", lambda msg, **kw: infos.append(msg))
        w = ZerobusWriter(_cfg())

        await w.setup()

        assert w._stream is stream and len(holder["sdk"].create_calls) == 1
        assert "Zerobus writer ready" in infos

    async def test_nonretriable_create_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deterministic config errors (bad OAuth creds, forbidden table) fail the deploy visibly."""
        exc = writer_mod.NonRetriableException("PERMISSION_DENIED: table not found")
        _patch_sdk(monkeypatch, _FakeStream(), fail_create=exc)
        w = ZerobusWriter(_cfg())

        with pytest.raises(writer_mod.NonRetriableException, match="PERMISSION_DENIED"):
            await w.setup()

        assert w._stream is None

    async def test_transient_create_failure_warns_and_first_batch_retries(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A transient/unknown failure warns (no 'ready' log), doesn't raise, and leaves the
        writer usable: the first batch reopens the stream and ingests."""
        stream = _FakeStream()
        holder = _patch_sdk(monkeypatch, stream, fail_create=ConnectionError("endpoint down"))
        warnings: list[str] = []
        infos: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "warning", lambda msg, **kw: warnings.append(msg))
        monkeypatch.setattr(writer_mod.logger, "info", lambda msg, **kw: infos.append(msg))
        w = ZerobusWriter(_cfg())

        await w.setup()                                # logs a warning, does not raise

        assert warnings == ["Zerobus unreachable at setup; buffering and retrying"]
        assert "Zerobus writer ready" not in infos and w._stream is None

        r = await w.write_batch(TestStreamLifecycle._FakeStore([_row(1.0)]), 100)

        assert r.n_rows == 1 and stream.flushed == 1
        assert len(holder["sdk"].create_calls) == 2    # failed setup attempt + first-batch retry


def test_with_scheme_adds_https_and_preserves_explicit_scheme() -> None:
    """_with_scheme prepends https:// to bare hostnames and leaves an explicit scheme alone."""
    assert writer_mod._with_scheme("host.databricks.com") == "https://host.databricks.com"
    assert writer_mod._with_scheme("https://host.databricks.com") == "https://host.databricks.com"
    assert writer_mod._with_scheme("http://host.databricks.com") == "http://host.databricks.com"
