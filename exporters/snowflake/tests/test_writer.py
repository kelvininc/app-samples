"""Unit tests for SnowflakeWriter's SQL building and connection handling (no cloud)."""
import threading
from datetime import datetime

import pytest
from snowflake.connector.errors import DatabaseError, OperationalError, ProgrammingError

import writer as writer_mod
from settings import Settings
from store import Records
from writer import SnowflakeWriter

SF = {"account": "acc", "user": "u", "warehouse": "WH", "database": "DB", "schema": "PUBLIC", "table": "EVENTS"}


def _cfg(auth=None):
    auth = auth or {"method": "password", "password": "pw"}
    return Settings(snowflake={**SF, "auth": auth}).snowflake


def _row(payload):
    return {"timestamp": datetime(2026, 1, 1), "asset": "a", "datastream": "d", "payload": payload}


class TestBuildInsert:
    def test_one_statement_with_parse_json_and_flat_params(self) -> None:
        w = SnowflakeWriter(_cfg())
        rows = [_row(42.5), _row("running")]
        query, params = w.build_insert(rows)
        assert query.startswith("INSERT INTO DB.PUBLIC.EVENTS (timestamp, asset, datastream, payload)")
        assert "PARSE_JSON(column4)" in query
        assert query.count("(?, ?, ?, ?)") == 2          # one value tuple per row
        assert len(params) == 8                           # 4 bound values per row, flat
        assert params[3] == "42.5" and params[7] == '"running"'

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_payload_becomes_null(self, bad: float) -> None:
        _, params = SnowflakeWriter(_cfg()).build_insert([_row(bad)])
        assert params[3] == "null"

    def test_rejects_empty_rows(self) -> None:
        with pytest.raises(ValueError, match="at least one row"):
            SnowflakeWriter(_cfg()).build_insert([])


class TestConnection:
    def test_password_auth_unwraps_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(writer_mod.snowflake.connector, "connect", lambda **kw: captured.update(kw) or object())
        SnowflakeWriter(_cfg())._connection()
        assert captured["password"] == "pw" and captured["account"] == "acc"
        assert captured["database"] == "DB" and captured["schema"] == "PUBLIC"

    def test_key_pair_auth_loads_der(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(writer_mod, "_private_key_der", lambda pem, passphrase: b"DER-BYTES")
        monkeypatch.setattr(writer_mod.snowflake.connector, "connect", lambda **kw: captured.update(kw) or object())
        SnowflakeWriter(_cfg(auth={"method": "key_pair", "private_key": "-----BEGIN-----"}))._connection()
        assert captured["private_key"] == b"DER-BYTES" and "password" not in captured

    def test_insert_resets_connection_on_failure(self) -> None:
        class BoomCursor:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, *a): raise RuntimeError("connection lost")

        class BoomCon:
            def cursor(self): return BoomCursor()
            def close(self): ...

        w = SnowflakeWriter(_cfg())
        w._con = BoomCon()
        with pytest.raises(RuntimeError, match="connection lost"):
            w._insert(Records([_row(1.0)], 1, 1, 0))
        assert w._con is None                             # reset -> retry reconnects


class TestSetupProbe:
    """setup() classifies probe failures: deterministic config errors (rejected auth,
    missing object, bad key material) raise so a bad deployment fails fast; transient
    and unclassified ones warn, reset the connection so no half-state is reused, and
    let the app start; the drain's retry/backoff owns connectivity from here on."""

    pytestmark = pytest.mark.asyncio

    class _Cursor:
        def __init__(self, fail: Exception | None) -> None:
            self.executed: list[str] = []
            self.fail = fail

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def execute(self, query: str, *a) -> None:
            self.executed.append(query)
            if self.fail:
                raise self.fail

    def _writer(self, fail: Exception | None = None):
        w = SnowflakeWriter(_cfg())
        cur = self._Cursor(fail)

        class Con:
            def cursor(self): return cur
            def close(self): ...

        w._con = Con()
        return w, cur

    async def test_probe_targets_fully_qualified_table(self) -> None:
        """The probe selects from the FQN with LIMIT 0: proves account + auth + network,
        and that the database/schema/table exists and is readable; without moving data."""
        w, cur = self._writer()
        await w.setup()
        assert cur.executed == ["SELECT 1 FROM DB.PUBLIC.EVENTS LIMIT 0"]
        assert w._con is not None                            # healthy probe keeps the connection

    async def test_missing_table_raises(self) -> None:
        """A missing database/schema/table surfaces as ProgrammingError (a DatabaseError
        subclass): deterministic config error, raise so the bad deployment fails fast."""
        w, _ = self._writer(fail=ProgrammingError("Object 'DB.PUBLIC.EVENTS' does not exist"))
        with pytest.raises(ProgrammingError):
            await w.setup()

    async def test_transient_operational_error_is_tolerated_and_resets(self) -> None:
        """A network-ish OperationalError doesn't raise: it warns and resets the
        connection so the drain's retry reconnects fresh instead of reusing half-state."""
        w, _ = self._writer(fail=OperationalError("could not reach account"))
        await w.setup()                                      # must not raise
        assert w._con is None                                # reset -> drain reconnects

    async def test_database_error_raises(self) -> None:
        """A server-side rejection (auth failure, missing object, no permission) is
        deterministic: raise so the bad deployment fails fast."""
        w, _ = self._writer(fail=DatabaseError("Incorrect username or password"))
        with pytest.raises(DatabaseError):
            await w.setup()

    async def test_bad_private_key_raises(self) -> None:
        """Malformed PEM / wrong passphrase surfaces as ValueError: config error, raise."""
        w, _ = self._writer(fail=ValueError("Could not deserialize key data"))
        with pytest.raises(ValueError):
            await w.setup()

    async def test_unknown_failure_is_tolerated_and_resets(self) -> None:
        """Anything unclassified is treated as transient: warn, reset, start anyway."""
        w, _ = self._writer(fail=OSError("connection timed out"))
        await w.setup()                                      # must not raise
        assert w._con is None                                # reset -> drain reconnects


class TestTeardownLock:
    """Every ``_con`` op holds the writer lock, so teardown's _reset must wait out an
    in-flight _insert running on a worker thread instead of closing under it."""

    def test_reset_waits_for_in_flight_insert(self) -> None:
        entered = threading.Event()                          # _insert reached execute (lock held)
        release = threading.Event()                          # let execute finish

        class SlowCursor:
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def execute(self, *a) -> None:
                entered.set()
                release.wait(timeout=5)                      # holds the writer lock while set

        class Con:
            def __init__(self) -> None: self.closed = False
            def cursor(self): return SlowCursor()
            def close(self) -> None: self.closed = True

        w = SnowflakeWriter(_cfg())
        con = Con()
        w._con = con

        errors: list[BaseException] = []

        def run(fn, *args) -> None:
            try:
                fn(*args)
            except BaseException as e:                       # surface worker failures in the test
                errors.append(e)

        insert = threading.Thread(target=run, args=(w._insert, Records([_row(1.0)], 1, 1, 0)))
        insert.start()
        assert entered.wait(timeout=5)                       # _insert is mid-execute, lock held

        reset = threading.Thread(target=run, args=(w._reset,))
        reset.start()
        reset.join(timeout=0.2)
        assert reset.is_alive()                              # _reset blocks behind the in-flight insert
        assert w._con is con and not con.closed              # nothing closed under the running statement

        release.set()
        insert.join(timeout=5)
        reset.join(timeout=5)
        assert not insert.is_alive() and not reset.is_alive()
        assert errors == []                                  # neither thread raised
        assert con.closed and w._con is None                 # reset ran only after the insert finished
