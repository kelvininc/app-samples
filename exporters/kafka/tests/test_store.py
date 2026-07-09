"""Unit tests for the DuckDB-backed Store buffer."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from store import PRIORITY_HIGH, PRIORITY_LOW, Store

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
        assert result.n_rows == 4 and len(result.seqs) == 4
        by_ds = {row["datastream"]: row["payload"] for row in result.rows}
        assert by_ds["temperature"] == 42.5 and isinstance(by_ds["temperature"], float)
        assert by_ds["status"] == "running"
        assert by_ds["enabled"] is True
        assert by_ds["label"] == "true" and isinstance(by_ds["label"], str)

    async def test_read_empty_buffer_reports_no_seqs(self, store: Store) -> None:
        """read() on an empty buffer returns no rows, an empty seq set, and zero count."""
        result = await store.read(10)
        assert result.rows == [] and result.seqs == [] and result.n_rows == 0

    async def test_read_respects_limit(self, seeded: Store) -> None:
        """read(limit) returns at most `limit` rows, oldest first by seq (fifo default)."""
        result = await seeded.read(2)
        assert result.n_rows == 2
        assert [row["datastream"] for row in result.rows] == ["temperature", "status"]


class TestPriority:
    """Per-stream priorities: selection order, defaults, and refresh."""

    @staticmethod
    async def _seed_normal_then_critical(store: Store) -> None:
        """Three older 'normal' rows, then two newer 'critical' rows."""
        base = datetime(2026, 1, 1)
        for i in range(3):
            await store.append(base + timedelta(seconds=i), "pump-1", "normal", float(i))
        for i in range(2):
            await store.append(base + timedelta(seconds=10 + i), "pump-1", "critical", float(i))

    async def test_high_priority_selected_before_older_normal_rows(self, store: Store) -> None:
        """A High stream fills the batch before any Normal row, whatever its age."""
        await self._seed_normal_then_critical(store)
        await store.set_priorities({("pump-1", "critical"): PRIORITY_HIGH})
        batch = await store.read(2)
        assert {row["datastream"] for row in batch.rows} == {"critical"}

    async def test_selected_rows_are_emitted_chronologically(self, store: Store) -> None:
        """Priority decides selection only: the batch itself comes back in seq order, so a
        High row selected ahead of an older Normal one is still emitted after it."""
        await self._seed_normal_then_critical(store)
        await store.set_priorities({("pump-1", "critical"): PRIORITY_HIGH})
        batch = await store.read(3)                     # 2 critical + oldest normal
        assert batch.seqs == sorted(batch.seqs)
        assert [row["datastream"] for row in batch.rows] == ["normal", "critical", "critical"]

    async def test_unset_priority_ranks_medium(self, store: Store) -> None:
        """An explicit Medium (2) doesn't outrank an unset stream: both are the same level,
        so fifo age decides."""
        base = datetime(2026, 1, 1)
        await store.append(base, "pump-1", "unset", 1.0)                      # older
        await store.append(base + timedelta(seconds=1), "pump-1", "explicit", 2.0)
        await store.set_priorities({("pump-1", "explicit"): 2})
        batch = await store.read(1)
        assert batch.rows[0]["datastream"] == "unset"

    async def test_low_priority_ranks_below_unset_streams(self, store: Store) -> None:
        """Low (3) explicitly demotes: an older Low row is selected after a newer stream
        that nobody configured (unset ranks Medium)."""
        base = datetime(2026, 1, 1)
        await store.append(base, "pump-1", "verbose", 1.0)                    # older, marked Low
        await store.append(base + timedelta(seconds=1), "pump-1", "unset", 2.0)
        await store.set_priorities({("pump-1", "verbose"): PRIORITY_LOW})
        batch = await store.read(1)
        assert batch.rows[0]["datastream"] == "unset"

    async def test_set_priorities_replaces_the_previous_map(self, store: Store) -> None:
        """A second set_priorities() fully replaces the first (redeploy semantics): the
        previously-High stream re-ranks Normal and age takes over again."""
        await self._seed_normal_then_critical(store)
        await store.set_priorities({("pump-1", "critical"): PRIORITY_HIGH})
        await store.set_priorities({})
        batch = await store.read(2)
        assert {row["datastream"] for row in batch.rows} == {"normal"}       # oldest again


class TestLifo:
    """order='lifo': newest rows are selected first, still emitted chronologically."""

    @staticmethod
    async def _seed_four(store: Store) -> None:
        base = datetime(2026, 1, 1)
        for i, name in enumerate(["a", "b", "c", "d"]):     # seqs 1..4, oldest to newest
            await store.append(base + timedelta(seconds=i), "pump-1", name, float(i))

    async def test_lifo_selects_newest_but_emits_chronologically(self, store: Store) -> None:
        store.order = "lifo"
        await self._seed_four(store)
        batch = await store.read(2)
        assert [row["datastream"] for row in batch.rows] == ["c", "d"]   # newest two, in time order
        assert batch.seqs == sorted(batch.seqs)

    async def test_lifo_drop_leaves_older_rows_for_the_next_batch(self, store: Store) -> None:
        """Dropping a LIFO batch removes exactly the newest rows; the older backlog survives
        and is picked up by the next read."""
        store.order = "lifo"
        await self._seed_four(store)
        batch = await store.read(2)
        await store.drop(batch.seqs)
        rest = await store.read(10)
        assert [row["datastream"] for row in rest.rows] == ["a", "b"]

    async def test_high_priority_old_row_beats_normal_new_row_under_lifo(self, store: Store) -> None:
        """Priority outranks recency: an old High row is selected before the newest Normal one."""
        store.order = "lifo"
        base = datetime(2026, 1, 1)
        await store.append(base, "pump-1", "critical", 1.0)                   # oldest
        await store.append(base + timedelta(seconds=1), "pump-1", "normal", 2.0)
        await store.set_priorities({("pump-1", "critical"): PRIORITY_HIGH})
        batch = await store.read(1)
        assert batch.rows[0]["datastream"] == "critical"


class TestReadToFile:
    """Streaming a batch to a file (file sinks)."""

    async def test_parquet_payload_is_scalar_json(self, seeded: Store, tmp_path: Path) -> None:
        """Parquet export writes each payload as type-preserving scalar JSON text."""
        duckdb = pytest.importorskip("duckdb")
        result = await seeded.read_to_file(str(tmp_path / "b.parquet"), "parquet", 10)
        assert result.n_rows == 4 and len(result.seqs) == 4
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

    async def test_lifo_file_batch_selects_newest_in_time_order(self, seeded: Store, tmp_path: Path) -> None:
        """The file path honors selection order too: a LIFO batch streams the newest rows,
        written chronologically."""
        seeded.order = "lifo"
        result = await seeded.read_to_file(str(tmp_path / "b.json"), "json", 2)
        assert len(result.seqs) == 2 and result.seqs == sorted(result.seqs)
        rows = [json.loads(line) for line in open(result.path) if line.strip()]
        assert [r["datastream"] for r in rows] == ["enabled", "label"]   # newest two, in time order

    async def test_empty_buffer_reports_no_seqs(self, store: Store, tmp_path: Path) -> None:
        """read_to_file() on an empty buffer returns an empty seq set and zero count."""
        result = await store.read_to_file(str(tmp_path / "e.parquet"), "parquet", 10)
        assert result.seqs == [] and result.n_rows == 0

    async def test_rejects_unknown_format(self, seeded: Store, tmp_path: Path) -> None:
        """read_to_file() raises for a format DuckDB COPY can't emit."""
        with pytest.raises(ValueError, match="Unsupported format"):
            await seeded.read_to_file(str(tmp_path / "x.avro"), "avro", 10)


class TestDrop:
    """Dropping a batch after a successful upload."""

    async def test_drop_removes_only_the_batch_seqs(self, seeded: Store) -> None:
        """drop(seqs) deletes the read batch and leaves the rest."""
        batch = await seeded.read(2)
        await seeded.drop(batch.seqs)
        rest = await seeded.read(10)
        assert rest.n_rows == 2 and {row["datastream"] for row in rest.rows} == {"enabled", "label"}

    async def test_drop_handles_a_non_contiguous_seq_set(self, store: Store) -> None:
        """A priority-selected batch is not a contiguous seq range; drop must delete exactly
        the selected seqs and nothing between them."""
        base = datetime(2026, 1, 1)
        await store.append(base, "pump-1", "critical", 1.0)                          # oldest
        await store.append(base + timedelta(seconds=1), "pump-1", "normal", 2.0)     # in between
        await store.append(base + timedelta(seconds=2), "pump-1", "critical", 3.0)   # newest
        normal_seq = next(s for s, r in zip((await store.read(10)).seqs, (await store.read(10)).rows)
                          if r["datastream"] == "normal")
        await store.set_priorities({("pump-1", "critical"): PRIORITY_HIGH})
        batch = await store.read(2)                    # the two critical rows around the normal one
        assert len(batch.seqs) == 2 and normal_seq not in batch.seqs
        assert min(batch.seqs) < normal_seq < max(batch.seqs)   # genuinely non-contiguous
        await store.drop(batch.seqs)
        rest = await store.read(10)
        assert rest.n_rows == 1 and rest.rows[0]["datastream"] == "normal"

    async def test_drop_never_removes_rows_appended_after_read(self, store: Store) -> None:
        """A row appended between read and drop has a seq outside the set and must survive."""
        base = datetime(2026, 1, 1)
        await store.append(base, "pump-1", "a", 1.0)
        await store.append(base + timedelta(seconds=1), "pump-1", "b", 2.0)
        batch = await store.read(100)
        await store.append(base + timedelta(seconds=2), "pump-1", "c", 3.0)  # late arrival
        await store.drop(batch.seqs)
        rest = await store.read(10)
        assert rest.n_rows == 1 and rest.rows[0]["datastream"] == "c"

    async def test_drop_of_empty_seq_set_is_a_noop(self, seeded: Store) -> None:
        """drop([]) (an empty upload) must not touch the buffer."""
        await seeded.drop([])
        assert (await seeded.read(10)).n_rows == 4


class TestCap:
    """Bounding the backlog on disk."""

    async def test_cap_keeps_newest_and_drops_overflow(self, seeded: Store) -> None:
        """cap(n) drops the oldest rows beyond n, keeping the newest n."""
        await seeded.cap(2)
        result = await seeded.read(10)
        assert result.n_rows == 2 and {row["datastream"] for row in result.rows} == {"enabled", "label"}

    async def test_cap_evicts_normal_rows_before_high_priority_ones(self, store: Store) -> None:
        """Eviction is lowest-priority-first: newer Normal rows go before older High rows."""
        base = datetime(2026, 1, 1)
        for i in range(2):                                                    # High rows, oldest
            await store.append(base + timedelta(seconds=i), "pump-1", "critical", float(i))
        for i in range(2):                                                    # Normal rows, newest
            await store.append(base + timedelta(seconds=10 + i), "pump-1", "normal", float(i))
        await store.set_priorities({("pump-1", "critical"): PRIORITY_HIGH})
        await store.cap(2)
        result = await store.read(10)
        assert {row["datastream"] for row in result.rows} == {"critical"}

    async def test_cap_evicts_low_before_unset_before_high(self, store: Store) -> None:
        """Eviction walks the levels bottom-up: Low goes first, then unset (Medium), and
        High rows survive the longest, regardless of age."""
        base = datetime(2026, 1, 1)
        await store.append(base, "pump-1", "critical", 1.0)                   # oldest, High
        await store.append(base + timedelta(seconds=1), "pump-1", "unset", 2.0)
        await store.append(base + timedelta(seconds=2), "pump-1", "verbose", 3.0)  # newest, Low
        await store.set_priorities({("pump-1", "critical"): PRIORITY_HIGH,
                                    ("pump-1", "verbose"): PRIORITY_LOW})
        await store.cap(2)
        assert {r["datastream"] for r in (await store.read(10)).rows} == {"critical", "unset"}
        await store.cap(1)
        assert [r["datastream"] for r in (await store.read(10)).rows] == ["critical"]

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

    async def test_aware_timestamp_is_stored_as_naive_utc(self, store: Store) -> None:
        """An aware timestamp is normalized to naive UTC before insert, so the stored value
        doesn't depend on the host/session timezone, and an aware duplicate of the same
        instant upserts instead of inserting a second row."""
        aware = datetime(2026, 1, 1, 14, 30, tzinfo=timezone(timedelta(hours=2)))  # 12:30 UTC
        await store.append(aware, "pump-1", "temperature", 1.0)
        result = await store.read(10)
        assert result.rows[0]["timestamp"] == datetime(2026, 1, 1, 12, 30)  # naive UTC
        await store.append(aware.astimezone(timezone.utc), "pump-1", "temperature", 2.0)
        result = await store.read(10)
        assert result.n_rows == 1 and result.rows[0]["payload"] == 2.0     # same key -> upsert

    async def test_upsert_after_read_requeues_and_survives_drop(self, store: Store) -> None:
        """A correction to an already-read key takes a fresh seq outside the in-flight set,
        so dropping that batch can't delete it: the new value survives to the next batch."""
        ts = datetime(2026, 1, 1)
        await store.append(ts, "pump-1", "temperature", 1.0)
        batch = await store.read(10)                       # seq set covers the seq=1 row
        await store.append(ts, "pump-1", "temperature", 2.0)  # correction lands mid-upload
        await store.drop(batch.seqs)                       # drop only what was sent
        rest = await store.read(10)
        assert rest.n_rows == 1 and rest.rows[0]["payload"] == 2.0
