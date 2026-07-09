"""Unit tests for the DuckDB-backed Store buffer."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from store import Store

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store() -> Store:
    """In-memory Store, set up and ready."""
    s = Store(":memory:")
    await s.setup()
    return s


@pytest_asyncio.fixture
async def seeded(store: Store) -> Store:
    """Store with one row per payload type, plus a string 'true' (not the bool)."""
    base = datetime(2026, 1, 1)
    await store.append(base, "pump-1", "temperature", 42.5)
    await store.append(base + timedelta(seconds=1), "pump-1", "status", "running")
    await store.append(base + timedelta(seconds=2), "pump-1", "enabled", True)
    await store.append(base + timedelta(seconds=3), "pump-1", "label", "true")
    return store


class TestRead:
    """Reading record batches from the buffer."""

    async def test_read_preserves_native_payload_types(self, seeded: Store) -> None:
        """read() returns each payload with its native Python type intact."""
        result = await seeded.read(10)
        assert result.n_rows == 4 and result.cursor is not None
        by_ds = {row["datastream"]: row["payload"] for row in result.rows}
        assert by_ds["temperature"] == 42.5 and isinstance(by_ds["temperature"], float)
        assert by_ds["status"] == "running"
        assert by_ds["enabled"] is True
        assert by_ds["label"] == "true" and isinstance(by_ds["label"], str)

    async def test_read_empty_buffer_reports_no_cursor(self, store: Store) -> None:
        """read() on an empty buffer returns no rows, a None cursor, and zero count."""
        result = await store.read(10)
        assert result.rows == [] and result.cursor is None and result.n_rows == 0

    async def test_read_respects_limit(self, seeded: Store) -> None:
        """read(limit) returns at most `limit` rows, oldest first by seq."""
        result = await seeded.read(2)
        assert result.n_rows == 2
        assert [row["datastream"] for row in result.rows] == ["temperature", "status"]


class TestReadToFile:
    """Streaming a batch to a file (file sinks)."""

    async def test_parquet_payload_is_scalar_json(self, seeded: Store, tmp_path: Path) -> None:
        """Parquet export writes each payload as type-preserving scalar JSON text."""
        duckdb = pytest.importorskip("duckdb")
        result = await seeded.read_to_file(str(tmp_path / "b.parquet"), "parquet", 10)
        assert result.n_rows == 4 and result.cursor is not None
        got = {r[0]: r[1] for r in duckdb.connect()
               .query(f"SELECT datastream, payload FROM read_parquet('{result.path}')").fetchall()}
        assert json.loads(got["temperature"]) == 42.5
        assert json.loads(got["enabled"]) is True       # 'true'
        assert json.loads(got["label"]) == "true"       # '"true"' -> stays a string

    async def test_json_is_native_typed_not_double_encoded(self, seeded: Store, tmp_path: Path) -> None:
        """JSON export embeds native values, not double-encoded strings."""
        result = await seeded.read_to_file(str(tmp_path / "b.json"), "json", 10)
        rows = [json.loads(line) for line in open(result.path) if line.strip()]
        by_ds = {r["datastream"]: r["payload"] for r in rows}
        assert by_ds["temperature"] == 42.5 and isinstance(by_ds["temperature"], float)
        assert by_ds["enabled"] is True
        assert by_ds["label"] == "true" and isinstance(by_ds["label"], str)

    async def test_csv_smoke(self, seeded: Store, tmp_path: Path) -> None:
        """CSV export writes the rows in a readable form."""
        result = await seeded.read_to_file(str(tmp_path / "b.csv"), "csv", 10)
        text = Path(result.path).read_text()
        assert "temperature" in text and "42.5" in text

    async def test_empty_buffer_reports_no_cursor(self, store: Store, tmp_path: Path) -> None:
        """read_to_file() on an empty buffer returns a None cursor and zero count."""
        result = await store.read_to_file(str(tmp_path / "e.parquet"), "parquet", 10)
        assert result.cursor is None and result.n_rows == 0

    async def test_rejects_unknown_format(self, seeded: Store, tmp_path: Path) -> None:
        """read_to_file() raises for a format DuckDB COPY can't emit."""
        with pytest.raises(ValueError, match="Unsupported format"):
            await seeded.read_to_file(str(tmp_path / "x.avro"), "avro", 10)


class TestDrop:
    """Dropping a batch after a successful upload."""

    async def test_drop_removes_only_up_to_cursor(self, seeded: Store) -> None:
        """drop(cursor) deletes the read batch and leaves the rest."""
        batch = await seeded.read(2)
        await seeded.drop(batch.cursor)
        rest = await seeded.read(10)
        assert rest.n_rows == 2 and {row["datastream"] for row in rest.rows} == {"enabled", "label"}

    async def test_drop_never_removes_rows_appended_after_read(self, store: Store) -> None:
        """A row appended between read and drop has a higher seq and must survive."""
        base = datetime(2026, 1, 1)
        await store.append(base, "pump-1", "a", 1.0)
        await store.append(base + timedelta(seconds=1), "pump-1", "b", 2.0)
        batch = await store.read(100)
        await store.append(base + timedelta(seconds=2), "pump-1", "c", 3.0)  # late arrival
        await store.drop(batch.cursor)
        rest = await store.read(10)
        assert rest.n_rows == 1 and rest.rows[0]["datastream"] == "c"


class TestCap:
    """Bounding the backlog on disk."""

    async def test_cap_keeps_newest_and_drops_overflow(self, seeded: Store) -> None:
        """cap(n) drops the oldest rows beyond n, keeping the newest n."""
        await seeded.cap(2)
        result = await seeded.read(10)
        assert result.n_rows == 2 and {row["datastream"] for row in result.rows} == {"enabled", "label"}

    async def test_cap_zero_is_noop(self, seeded: Store) -> None:
        """cap(0) is unbounded and removes nothing."""
        await seeded.cap(0)
        assert (await seeded.read(10)).n_rows == 4


class TestSetup:
    """Buffer lifecycle."""

    async def test_setup_is_idempotent(self, store: Store) -> None:
        """A second setup() reuses the open connection (so an on_connect re-fire won't leak)."""
        con = store._con
        await store.setup()
        assert store._con is con

    async def test_teardown_closes_then_setup_reopens(self, store: Store) -> None:
        """teardown() closes the connection; setup() can reopen it."""
        await store.teardown()
        assert store._con is None
        await store.setup()
        assert store._con is not None


class TestAppend:
    """Buffering incoming rows."""

    async def test_append_upserts_on_conflict(self, store: Store) -> None:
        """A second append on the same (timestamp, asset, datastream) updates the payload."""
        ts = datetime(2026, 1, 1)
        await store.append(ts, "pump-1", "temperature", 1.0)
        await store.append(ts, "pump-1", "temperature", 2.0)
        result = await store.read(10)
        assert result.n_rows == 1 and result.rows[0]["payload"] == 2.0

    async def test_append_normalizes_aware_timestamps_to_naive_utc(self, store: Store) -> None:
        """An aware timestamp is stored as its naive-UTC equivalent, independent of the
        DuckDB session timezone."""
        aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))   # 10:00 UTC
        await store.append(aware, "pump-1", "temperature", 1.0)
        result = await store.read(10)
        assert result.rows[0]["timestamp"] == datetime(2026, 1, 1, 10, 0)

    async def test_upsert_after_read_requeues_and_survives_drop(self, store: Store) -> None:
        """A correction to an already-read key takes a fresh seq, so dropping the
        in-flight cursor can't delete it: the new value survives to the next batch."""
        ts = datetime(2026, 1, 1)
        await store.append(ts, "pump-1", "temperature", 1.0)
        batch = await store.read(10)                       # cursor covers the seq=1 row
        await store.append(ts, "pump-1", "temperature", 2.0)  # correction lands mid-upload
        await store.drop(batch.cursor)                     # drop only what was sent
        rest = await store.read(10)
        assert rest.n_rows == 1 and rest.rows[0]["payload"] == 2.0
