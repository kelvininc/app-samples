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
    """setup() runs a bounded startup probe. Deterministic config errors (rejected auth,
    missing object, bad key material) raise on the first attempt so a bad deployment fails
    fast. A transient-looking failure (network, unresolvable host from a wrong account) is
    retried a small number of times: if it recovers the app starts; if it never once
    connects the probe gives up and raises, so the deploy fails loudly instead of the drain
    retrying forever with data that can never be delivered."""

    pytestmark = pytest.mark.asyncio

    def _writer(self, monkeypatch: pytest.MonkeyPatch, exc: Exception | None = None,
                fails: int = 0, attempts: int = 3):
        """A writer whose probe connection raises ``exc`` on its first ``fails`` executes
        then succeeds. connect() is patched so each reconnect after a reset gets a fresh
        connection, and the fail counter persists across reconnects."""
        state = {"n": 0}
        executed: list[str] = []

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def execute(self, query: str, *a) -> None:
                executed.append(query)
                if exc is not None and state["n"] < fails:
                    state["n"] += 1
                    raise exc

        class Con:
            def cursor(self): return Cursor()
            def close(self): ...

        monkeypatch.setattr(writer_mod.snowflake.connector, "connect", lambda **kw: Con())
        w = SnowflakeWriter(_cfg(), probe_attempts=attempts, probe_delay=0)
        return w, executed

    async def test_probe_targets_fully_qualified_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The probe selects from the FQN with LIMIT 0: proves account + auth + network,
        and that the database/schema/table exists and is readable; without moving data."""
        w, executed = self._writer(monkeypatch)
        await w.setup()
        assert executed == ["SELECT 1 FROM DB.PUBLIC.EVENTS LIMIT 0"]
        assert w._con is not None                            # healthy probe keeps the connection

    @pytest.mark.parametrize("exc", [
        ProgrammingError("Object 'DB.PUBLIC.EVENTS' does not exist"),   # missing object (DatabaseError subclass)
        DatabaseError("Incorrect username or password"),               # rejected auth
        ValueError("Could not deserialize key data"),                  # malformed input
    ])
    async def test_deterministic_error_fails_fast(self, monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
        """Deterministic config errors (missing database/schema/table, rejected auth,
        malformed input) raise on the first attempt so the bad deployment fails fast."""
        w, executed = self._writer(monkeypatch, exc=exc, fails=99)
        with pytest.raises(type(exc)):
            await w.setup()
        assert len(executed) == 1                            # deterministic: no retry

    async def test_transient_operational_error_recovers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A network-ish OperationalError that clears within the probe window doesn't fail
        the deploy: the probe retries and the app starts once a later attempt connects."""
        w, executed = self._writer(monkeypatch, exc=OperationalError("could not reach account"),
                                   fails=2, attempts=3)
        await w.setup()                                      # 3rd attempt connects
        assert len(executed) == 3 and w._con is not None     # recovered; connection kept

    @pytest.mark.parametrize("exc", [
        OperationalError("could not reach account"),         # wrong/nonexistent account never resolves
        OSError("connection timed out"),                     # unclassified failure that persists
    ])
    async def test_persistent_failure_is_fatal(self, monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
        """A failure that never clears across the whole probe (a wrong account that never
        resolves, or an unclassified error that persists) is fatal: the bounded probe gives
        up and raises so the deploy fails, rather than the drain treating it as transient and
        retrying forever without delivering, or starting up only to stall."""
        w, executed = self._writer(monkeypatch, exc=exc, fails=99, attempts=3)
        with pytest.raises(type(exc)):
            await w.setup()
        assert len(executed) == 3 and w._con is None         # exhausted attempts; reset

    async def test_encrypted_key_without_passphrase_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An encrypted private key supplied without a passphrase makes cryptography's
        load_pem_private_key raise TypeError in the key-load path. That's a deterministic
        config error, so setup() must fail on the first attempt (normalized to
        KeyMaterialError) instead of burning every probe attempt retrying."""
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            raise TypeError("Password was not given but private key is encrypted")

        monkeypatch.setattr(writer_mod.serialization, "load_pem_private_key", boom)
        monkeypatch.setattr(writer_mod.snowflake.connector, "connect",
                            lambda **kw: pytest.fail("connect reached despite bad key material"))
        w = SnowflakeWriter(
            _cfg(auth={"method": "key_pair", "private_key": "-----BEGIN ENCRYPTED PRIVATE KEY-----"}),
            probe_attempts=3, probe_delay=0,
        )
        with pytest.raises(writer_mod.KeyMaterialError):
            await w.setup()
        assert calls["n"] == 1                               # deterministic: first attempt, no retry


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
