"""Unit tests for DeltaTableWriter's SQL building and connection handling (no cloud)."""
from datetime import datetime

import pytest
from databricks.sql.exc import ServerOperationError

import writer as writer_mod
from settings import Settings
from writer import DeltaTableWriter


def _cfg():
    """A minimal valid Databricks config (access_token auth)."""
    return Settings(databricks={"server_hostname": "h", "http_path": "/p", "delta_table": "c.s.t",
                                "auth": {"method": "access_token", "access_token": "tok"}}).databricks


def _row(payload):
    return {"timestamp": datetime(2026, 1, 1), "asset": "a", "datastream": "d", "payload": payload}


def test_build_insert_is_one_atomic_statement_with_flat_params() -> None:
    """build_insert emits a single multi-row INSERT and a flat, JSON-encoded param list."""
    rows = [
        {"timestamp": datetime(2026, 1, 1), "asset": "p1", "datastream": "temp", "payload": 42.5},
        {"timestamp": datetime(2026, 1, 1), "asset": "p1", "datastream": "label", "payload": "true"},
    ]
    query, params = DeltaTableWriter.build_insert("c.s.t", rows)
    assert query.startswith("INSERT INTO c.s.t (timestamp, asset, datastream, payload) VALUES ")
    assert query.count("parse_json(?)") == 2        # one value-tuple per row, one statement
    assert len(params) == 8                          # 4 bound values per row, flat
    assert params[3] == "42.5"                       # json.dumps(42.5)
    assert params[7] == '"true"'                     # json.dumps("true") stays a string


def test_build_insert_emits_markers_and_passes_values_separately() -> None:
    """A malicious-looking payload stays out of the SQL text build_insert emits: the query
    carries only ? markers and the payload is returned separately as one JSON param."""
    rows = [{"timestamp": datetime(2026, 1, 1), "asset": "a", "datastream": "d",
             "payload": "'); DROP TABLE x;--"}]
    query, params = DeltaTableWriter.build_insert("c.s.t", rows)
    assert "DROP TABLE" not in query
    assert params[3] == '"\'); DROP TABLE x;--"'     # whole thing is one bound JSON string


def test_build_insert_rejects_empty_rows() -> None:
    """An empty batch violates the caller's contract and fails loudly, not as malformed SQL."""
    with pytest.raises(ValueError, match="at least one row"):
        DeltaTableWriter.build_insert("c.s.t", [])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_build_insert_coerces_non_finite_to_null(bad: float) -> None:
    """NaN/Inf would make json.dumps emit invalid JSON and fail the whole batch; they become null."""
    _, params = DeltaTableWriter.build_insert("c.s.t", [_row(bad)])
    assert params[3] == "null"


class TestSetupProbe:
    """setup() probes the table itself and classifies failures: deterministic config
    errors raise (fail fast); transient/unclassified ones warn and let the app start."""

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
        w = DeltaTableWriter(_cfg())
        cur = self._Cursor(fail)

        class Con:
            def cursor(self): return cur
            def close(self): ...

        w._con = Con()
        return w, cur

    async def test_probe_targets_the_configured_table(self) -> None:
        """The probe is table-level (LIMIT 0), not a bare SELECT 1, so a typo'd
        delta_table or a missing grant surfaces at startup instead of on first upload."""
        w, cur = self._writer()
        await w.setup()
        assert cur.executed == ["SELECT 1 FROM c.s.t LIMIT 0"]

    async def test_server_rejection_raises(self) -> None:
        """A server-side rejection (missing table, missing grant) is deterministic: raise."""
        w, _ = self._writer(fail=ServerOperationError("TABLE_OR_VIEW_NOT_FOUND"))
        with pytest.raises(ServerOperationError):
            await w.setup()

    async def test_refused_credentials_raise(self) -> None:
        """A 401-style auth failure is a config error even when wrapped in a generic type."""
        w, _ = self._writer(fail=RuntimeError("Error during request to server: 401 Unauthorized"))
        with pytest.raises(RuntimeError, match="401"):
            await w.setup()

    async def test_transient_failure_is_tolerated_and_resets(self) -> None:
        """A network-ish/unclassified failure doesn't raise: it warns and resets the
        connection so the drain's retry reconnects fresh and owns connectivity."""
        w, _ = self._writer(fail=OSError("connection timed out"))
        await w.setup()                                      # must not raise
        assert w._con is None                                # reset -> drain reconnects


class TestConnection:
    """Lazy connect, reuse, and reset-on-failure (the retry's reconnect contract)."""

    class _FakeSql:
        def __init__(self) -> None:
            self.n, self.kw = 0, None

        def connect(self, **kw):
            self.n += 1
            self.kw = kw
            return object()

    def test_connection_is_built_once_and_rebuilt_after_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_connection builds via sql.connect once, reuses it, and rebuilds only after _reset."""
        fake = self._FakeSql()
        monkeypatch.setattr(writer_mod, "sql", fake)
        w = DeltaTableWriter(_cfg())
        c1, c2 = w._connection(), w._connection()
        assert c1 is c2 and fake.n == 1                       # reused, not reconnected per call
        assert fake.kw["access_token"] == "tok"              # SecretStr unwrapped for the driver
        # Inline params lift the 255-native-marker cap so batch_size 1000 fits one INSERT;
        # "silent" is the connector's documented value for "inline, without the usage warning".
        assert fake.kw["use_inline_params"] == "silent"
        w._reset()
        assert w._connection() is not c1 and fake.n == 2     # rebuilt after a reset

    def test_connection_oauth_builds_service_principal_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OAuth auth wires a service-principal credentials provider (host + unwrapped
        client credentials) into sql.connect and keeps inline params on ("silent")."""
        fake = self._FakeSql()
        provider = object()
        conf_kwargs: dict = {}

        def fake_config(**kw):
            conf_kwargs.update(kw)
            return "conf"

        monkeypatch.setattr(writer_mod, "sql", fake)
        monkeypatch.setattr(writer_mod, "Config", fake_config)
        # oauth_service_principal would fetch OIDC endpoints over HTTP; stub it out.
        monkeypatch.setattr(writer_mod, "oauth_service_principal",
                            lambda conf: provider if conf == "conf" else None)
        cfg = Settings(databricks={"server_hostname": "h", "http_path": "/p", "delta_table": "c.s.t",
                                   "auth": {"method": "oauth", "client_id": "cid",
                                            "client_secret": "sec"}}).databricks
        w = DeltaTableWriter(cfg)
        w._connection()
        assert conf_kwargs == {"host": "https://h", "client_id": "cid", "client_secret": "sec"}
        assert fake.kw["credentials_provider"] is provider   # the stubbed provider reached the driver
        assert fake.kw["use_inline_params"] == "silent"      # inline params on for the OAuth path too

    def test_insert_resets_connection_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed INSERT drops the (stale) connection so the next attempt reconnects, and re-raises."""
        class BoomCursor:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, *a): raise RuntimeError("connection lost")

        class BoomCon:
            def cursor(self): return BoomCursor()
            def close(self): ...

        w = DeltaTableWriter(_cfg())
        w._con = BoomCon()
        with pytest.raises(RuntimeError, match="connection lost"):
            w._insert([_row(1.0)])
        assert w._con is None                                # reset -> retry will reconnect
