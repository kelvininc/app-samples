import asyncio
import threading
from datetime import timezone
from typing import Literal, NamedTuple, Optional, Union

import duckdb
from kelvin.logs import logger

Scalar = Union[float, str, bool]

PRIORITY_HIGH = 1
PRIORITY_MEDIUM = 2        # also the rank of any stream without an explicit priority
PRIORITY_LOW = 3

# Project the UNION payload to one clean scalar-JSON string for file exports:
#   number -> 42.5   string -> "running"   boolean -> true
# Disambiguates the string "true" ('"true"') from the boolean true ('true').
_PAYLOAD_JSON = """
CASE union_tag(payload)
    WHEN 'num'  THEN to_json(union_extract(payload, 'num'))
    WHEN 'str'  THEN to_json(union_extract(payload, 'str'))
    WHEN 'bool' THEN to_json(union_extract(payload, 'bool'))
END
"""

# Batch SELECTION: priority decides which rows make the batch (High, then Medium/unset, then Low),
# then `order` decides which end of each level wins (fifo = oldest seqs, lifo = newest).
# EMISSION is a separate concern: callers re-sort the selected rows by seq ASC so every
# batch is produced/written in chronological order no matter how it was selected.
_BATCH = """
SELECT b.seq, b.timestamp, b.asset, b.datastream, b.payload
FROM buffer b
LEFT JOIN priorities p ON b.asset = p.asset AND b.datastream = p.datastream
ORDER BY COALESCE(p.priority, {medium}) ASC, b.seq {seq_dir}
LIMIT {limit}
"""


class Records(NamedTuple):
    rows: list[dict]
    seqs: list[int]            # seqs of the selected rows (the drop set); empty if none
    n_rows: int                # not `count`: that shadows tuple.count() (a real footgun)
    backlog: int               # rows still buffered once this batch is cleared


class FileRecords(NamedTuple):
    path: str                  # rows were streamed here
    seqs: list[int]
    n_rows: int
    backlog: int


class Store:
    """DuckDB-backed buffer. Stores number/string/boolean payloads in a single UNION
    column. Read a batch (rows or streamed to a file) -> upload -> drop(seqs).

    One connection for the store's lifetime (so :memory: survives across calls),
    guarded by a lock because every op runs in an asyncio.to_thread worker and the
    append path (@app.stream) and the drain task hit the connection concurrently.

    Draining is read-then-drop by explicit seq sets, never by row count or position: a
    read records the exact seqs it returned; ``drop`` deletes those rows and nothing
    else. Rows appended during a slow upload take fresh seqs outside the set and
    survive. An upsert that lands on an already-read key also takes a fresh seq, so a
    corrected value re-queues instead of being deleted with the in-flight batch.

    Batch selection honors ``order`` ("fifo" = oldest first, "lifo" = newest first) and
    the per-stream ``priorities`` table (``set_priorities``): High (1) before Medium (2)
    before Low (3), whatever their age; unset streams rank Medium. Selection only decides which
    rows make the batch; the rows themselves always come back in chronological (seq)
    order, so files and produced batches read in time order.

    Every read also reports ``backlog`` (rows that remain once the batch is cleared),
    counted in the same locked snapshot as the batch, so upload logs can show whether
    the exporter is keeping up.
    """

    def __init__(self, db_path: str = ":memory:", order: Literal["fifo", "lifo"] = "fifo"):
        self.db_path = db_path
        self.order = order          # reassigned in on_connect, before the drain task reads it
        self._con: Optional[duckdb.DuckDBPyConnection] = None
        self._lock = threading.Lock()

    def _conn(self) -> "duckdb.DuckDBPyConnection":
        # Non-None accessor for the data ops (which all run after setup()): makes the
        # connection's liveness explicit to readers and the type checker alike.
        if self._con is None:
            raise RuntimeError("Store.setup() must be called before use")
        return self._con

    def _total(self) -> int:
        # Callers hold self._lock. COUNT(*) on the buffer: cheap even at max_backlog scale.
        row = self._conn().execute("SELECT COUNT(*) FROM buffer").fetchone()
        return row[0] if row else 0     # COUNT(*) always returns a row

    def _batch_sql(self, limit: int) -> str:
        # limit is int-cast (never user text); DuckDB can't bind inside a COPY's subquery,
        # so the whole batch template is interpolated for both the record and file paths.
        seq_dir = "DESC" if self.order == "lifo" else "ASC"
        return _BATCH.format(medium=PRIORITY_MEDIUM, seq_dir=seq_dir, limit=int(limit))

    async def setup(self) -> None:
        await asyncio.to_thread(self._setup)

    def _setup(self) -> None:
        with self._lock:                 # same lock discipline as every other _con op
            if self._con is not None:    # idempotent: a re-run reuses the open connection
                return
            self._con = duckdb.connect(self.db_path)
            self._conn().execute("CREATE SEQUENCE IF NOT EXISTS seq START 1")
            self._conn().execute(
                """
                CREATE TABLE IF NOT EXISTS buffer (
                    seq BIGINT DEFAULT nextval('seq'),
                    timestamp DATETIME,
                    asset STRING,
                    datastream STRING,
                    payload UNION(num DOUBLE, str VARCHAR, bool BOOLEAN),
                    PRIMARY KEY (timestamp, asset, datastream)
                )
                """
            )
            self._conn().execute(
                """
                CREATE TABLE IF NOT EXISTS priorities (
                    asset STRING,
                    datastream STRING,
                    priority TINYINT,
                    PRIMARY KEY (asset, datastream)
                )
                """
            )
            # Rows carried over on the persistent volume: surfaced at startup because
            # shutdown logs are the ones that get lost (crash, OOM, log shipper stopping).
            pending = self._total()
        logger.info("Buffer ready", db_path=self.db_path, pending_rows=pending)

    async def teardown(self) -> None:
        await asyncio.to_thread(self._teardown)

    def _teardown(self) -> None:
        # Hold the lock so close() waits out any in-flight read/drop on a lingering worker
        # thread (a cancelled drain's to_thread op keeps running): closing mid-query would be
        # a use-after-close on the native connection.
        with self._lock:
            if self._con is None:
                return
            self._con.close()            # checkpoints the WAL into the persistent db file
            self._con = None
        logger.info("Buffer closed", db_path=self.db_path)

    async def set_priorities(self, priorities: dict[tuple[str, str], int]) -> None:
        await asyncio.to_thread(self._set_priorities, priorities)

    def _set_priorities(self, priorities: dict[tuple[str, str], int]) -> None:
        # Full replace on every connect: the table mirrors the current IO configuration, and
        # already-buffered rows re-rank against it on the next read (no stale per-row copies).
        with self._lock:
            self._conn().execute("DELETE FROM priorities")
            if priorities:
                self._conn().executemany(
                    "INSERT INTO priorities (asset, datastream, priority) VALUES (?, ?, ?)",
                    [(asset, stream, priority) for (asset, stream), priority in priorities.items()],
                )
        logger.info("Stream priorities set", count=len(priorities))

    async def append(self, timestamp, asset: str, datastream: str, payload: Scalar) -> None:
        await asyncio.to_thread(self._append, timestamp, asset, datastream, payload)

    def _append(self, timestamp, asset, datastream, payload) -> None:
        # DuckDB converts aware datetimes to the session timezone's wall time on insert;
        # normalize to naive UTC so exports don't depend on the host TZ.
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._conn().execute(
                """
                INSERT INTO buffer (timestamp, asset, datastream, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (timestamp, asset, datastream)
                    DO UPDATE SET payload = excluded.payload, seq = nextval('seq')
                """,
                (timestamp, asset, datastream, payload),
            )

    async def count(self) -> int:
        return await asyncio.to_thread(self._count)

    def _count(self) -> int:
        with self._lock:
            return self._total()

    async def read(self, limit: int) -> Records:
        return await asyncio.to_thread(self._read, limit)

    def _read(self, limit: int) -> Records:
        with self._lock:
            rows = self._conn().execute(
                f"SELECT seq, timestamp, asset, datastream, payload FROM ({self._batch_sql(limit)}) ORDER BY seq ASC"
            ).fetchall()
            total = self._total()
        data = [{"timestamp": r[1], "asset": r[2], "datastream": r[3], "payload": r[4]} for r in rows]
        return Records(data, [r[0] for r in rows], len(data), total - len(data))

    async def read_to_file(self, path: str, fmt: str, limit: int) -> FileRecords:
        return await asyncio.to_thread(self._read_to_file, path, fmt, limit)

    def _read_to_file(self, path: str, fmt: str, limit: int) -> FileRecords:
        if fmt not in ("parquet", "csv", "json"):
            raise ValueError(f"Unsupported format: {fmt!r}")
        # payload is scalar-JSON *text*. For json, CAST to JSON so it embeds raw (42.5, not "42.5").
        payload_expr = f"CAST({_PAYLOAD_JSON} AS JSON)" if fmt == "json" else _PAYLOAD_JSON
        batch = self._batch_sql(limit)
        with self._lock:
            # Same batch query twice (seqs, then COPY) is consistent: the lock excludes writers,
            # and the ORDER BY ends on the unique seq, so the selection is deterministic.
            seqs = [r[0] for r in
                    self._conn().execute(f"SELECT seq FROM ({batch}) ORDER BY seq ASC").fetchall()]
            if not seqs:
                return FileRecords(path, [], 0, 0)
            total = self._total()
            select = (f"SELECT timestamp, asset, datastream, {payload_expr} AS payload "
                      f"FROM ({batch}) ORDER BY seq ASC")
            # path is app-generated (never user input); DuckDB COPY can't bind the path. Streams to disk.
            self._conn().execute(f"COPY ({select}) TO '{path}' (FORMAT {fmt})")
        logger.info("Batch staged to temporary file", rows=len(seqs), file=path, format=fmt)
        return FileRecords(path, seqs, len(seqs), total - len(seqs))

    async def drop(self, seqs: list[int]) -> None:
        if seqs:
            await asyncio.to_thread(self._drop, seqs)

    def _drop(self, seqs: list[int]) -> None:
        with self._lock:
            self._conn().execute("DELETE FROM buffer WHERE seq IN (SELECT unnest(?))", (seqs,))
        # Housekeeping after a confirmed upload, NOT data loss: loss logs say "discarded".
        logger.info("Cleared uploaded rows from buffer", rows=len(seqs))

    async def cap(self, max_backlog: int) -> None:
        if max_backlog:
            await asyncio.to_thread(self._cap, max_backlog)

    def _cap(self, max_backlog: int) -> None:
        with self._lock:
            overflow = self._total() - max_backlog
            if overflow <= 0:
                return
            # Evict lowest-priority first, oldest within a level: an outage should cost
            # Low rows before Medium ones, and Medium before a single High one.
            self._conn().execute(
                f"""
                DELETE FROM buffer WHERE seq IN (
                    SELECT b.seq FROM buffer b
                    LEFT JOIN priorities p ON b.asset = p.asset AND b.datastream = p.datastream
                    ORDER BY COALESCE(p.priority, {PRIORITY_MEDIUM}) DESC, b.seq ASC
                    LIMIT ?
                )
                """,
                (overflow,),
            )
        logger.warning("Buffer over max_backlog; discarded lowest-priority oldest unsent rows",
                       discarded=overflow, max_backlog=max_backlog)
