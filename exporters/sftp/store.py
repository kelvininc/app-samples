import asyncio
import threading
from datetime import timezone
from typing import NamedTuple, Optional, Union

import duckdb
from kelvin.logs import logger

Scalar = Union[float, str, bool]

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


class Records(NamedTuple):
    rows: list[dict]
    cursor: Optional[int]      # highest seq read; None if the buffer was empty
    n_rows: int                # not `count`: that shadows tuple.count() (a real footgun)
    backlog: int               # rows still buffered once this batch is cleared


class FileRecords(NamedTuple):
    path: str                  # rows were streamed here
    cursor: Optional[int]
    n_rows: int
    backlog: int


class Store:
    """DuckDB-backed buffer. Stores number/string/boolean payloads in a single UNION
    column. Read a batch (rows or streamed to a file) -> upload -> drop(cursor).

    One connection for the store's lifetime (so :memory: survives across calls),
    guarded by a lock because every op runs in an asyncio.to_thread worker and the
    append path (@app.stream) and the drain task hit the connection concurrently.

    Draining is read-then-drop by a monotonic ``seq`` cursor, never by row count: a
    read records the highest seq it returned; ``drop`` deletes only ``seq <= cursor``.
    Rows appended during a slow upload get a higher seq and survive. An upsert that
    lands on an already-read key also takes a fresh seq, so a corrected value re-queues
    instead of being dropped under the in-flight cursor.

    Every read also reports ``backlog`` (rows that remain once the batch is cleared),
    counted in the same locked snapshot as the batch, so upload logs can show whether
    the exporter is keeping up.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
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
                "SELECT seq, timestamp, asset, datastream, payload FROM buffer ORDER BY seq ASC LIMIT ?",
                (limit,),
            ).fetchall()
            total = self._total()
        data = [{"timestamp": r[1], "asset": r[2], "datastream": r[3], "payload": r[4]} for r in rows]
        return Records(data, rows[-1][0] if rows else None, len(data), total - len(data))

    async def read_to_file(self, path: str, fmt: str, limit: int) -> FileRecords:
        return await asyncio.to_thread(self._read_to_file, path, fmt, limit)

    def _read_to_file(self, path: str, fmt: str, limit: int) -> FileRecords:
        if fmt not in ("parquet", "csv", "json"):
            raise ValueError(f"Unsupported format: {fmt!r}")
        # payload is scalar-JSON *text*. For json, CAST to JSON so it embeds raw (42.5, not "42.5").
        payload_expr = f"CAST({_PAYLOAD_JSON} AS JSON)" if fmt == "json" else _PAYLOAD_JSON
        batch = f"SELECT * FROM buffer ORDER BY seq ASC LIMIT {int(limit)}"
        with self._lock:
            row = self._conn().execute(f"SELECT COUNT(*), MAX(seq) FROM ({batch})").fetchone()
            count, cursor = row if row else (0, None)   # COUNT(*) always returns a row
            if not count:
                return FileRecords(path, None, 0, 0)
            total = self._total()
            select = f"SELECT timestamp, asset, datastream, {payload_expr} AS payload FROM ({batch})"
            # path is app-generated (never user input); DuckDB COPY can't bind the path. Streams to disk.
            self._conn().execute(f"COPY ({select}) TO '{path}' (FORMAT {fmt})")
        logger.info("Batch staged to temporary file", rows=count, file=path, format=fmt)
        return FileRecords(path, cursor, count, total - count)

    async def drop(self, cursor: int) -> None:
        await asyncio.to_thread(self._drop, cursor)

    def _drop(self, cursor: int) -> None:
        with self._lock:
            row = self._conn().execute("DELETE FROM buffer WHERE seq <= ?", (cursor,)).fetchone()
        # Housekeeping after a confirmed upload, NOT data loss: loss logs say "discarded".
        logger.info("Cleared uploaded rows from buffer", rows=row[0] if row else 0)

    async def cap(self, max_backlog: int) -> None:
        if max_backlog:
            await asyncio.to_thread(self._cap, max_backlog)

    def _cap(self, max_backlog: int) -> None:
        with self._lock:
            overflow = self._total() - max_backlog
            if overflow <= 0:
                return
            self._conn().execute(
                "DELETE FROM buffer WHERE seq IN (SELECT seq FROM buffer ORDER BY seq ASC LIMIT ?)",
                (overflow,),
            )
        logger.warning("Buffer over max_backlog; discarded oldest unsent rows",
                       discarded=overflow, max_backlog=max_backlog)
