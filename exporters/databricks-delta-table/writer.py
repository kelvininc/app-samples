import asyncio
import json
import math

from databricks import sql
# oauth_service_principal is the documented Databricks helper but isn't re-exported in the stubs.
from databricks.sdk.core import Config, oauth_service_principal  # pyright: ignore[reportPrivateImportUsage]
from databricks.sql.exc import ServerOperationError
from kelvin.logs import logger

from settings import Databricks
from store import Records, Store

USER_AGENT = "kelvin.ai"


def _fatal_setup_error(e: Exception) -> bool:
    """Deterministic config errors the drain can't retry away. ServerOperationError means
    the probe reached the warehouse and was rejected (missing table, missing grant);
    401/403-style messages mean the credentials themselves were refused."""
    if isinstance(e, ServerOperationError):
        return True
    msg = str(e).lower()
    return any(s in msg for s in ("401", "403", "unauthorized", "invalid access token", "permission denied"))


def _payload_json(payload) -> str:
    """JSON-encode a scalar payload for parse_json(). Non-finite floats (NaN/Inf) become
    null: json.dumps would otherwise emit literal NaN/Infinity, which parse_json rejects,
    failing the whole batch and stalling the buffer behind one poison row."""
    if isinstance(payload, float) and not math.isfinite(payload):
        return "null"
    return json.dumps(payload)


class DeltaTableWriter:
    """Record sink: read a batch from the buffer and INSERT it into the Delta table.

    databricks-sql is synchronous, so the blocking calls run in asyncio.to_thread.
    The connection is reused across ticks and rebuilt after a failed write.
    """

    fmt = None      # record sink: wants dict rows, not a file

    def __init__(self, cfg: Databricks):
        self.cfg = cfg
        self._con = None

    @staticmethod
    def build_insert(table: str, rows: list[dict]) -> tuple[str, list]:
        """Return one atomic multi-row INSERT and its flat parameter list.

        A single statement (not executemany) so the batch commits whole or not at all:
        a connection drop mid-statement leaves nothing committed, so no partial batch
        lands. Recovery is at-least-once, not exactly-once: if the server commits but the
        ack is lost, the batch isn't dropped and the retry re-sends it. Delta enforces no
        key, so a re-sent batch duplicates rows; downstream queries should tolerate that.
        Table is pre-validated by Settings; values are passed as parameters; the connector
        escapes and inlines them (use_inline_params); app code never splices raw values into SQL.
        """
        if not rows:
            raise ValueError("build_insert requires at least one row")
        values = ", ".join(["(?, ?, ?, parse_json(?))"] * len(rows))
        query = f"INSERT INTO {table} (timestamp, asset, datastream, payload) VALUES {values}"
        params: list = []
        for r in rows:
            params += [r["timestamp"], r["asset"], r["datastream"], _payload_json(r["payload"])]
        return query, params

    async def setup(self) -> None:
        """Startup probe policy: deterministic config errors (rejected credentials, missing
        table, missing grant) raise so a bad deployment fails fast; transient network errors
        and anything unclassified log a warning and let the app start; the drain's
        retry/backoff owns connectivity from here on."""
        try:
            await asyncio.to_thread(self._validate)
        except Exception as e:
            if _fatal_setup_error(e):
                raise
            await asyncio.to_thread(self._reset)        # drop the possibly-stale connection; drain reconnects
            logger.warning("Delta Table probe failed; starting anyway (drain will retry)",
                           error=str(e), error_type=type(e).__name__)

    def _validate(self) -> None:
        # Probe the table itself, not just SELECT 1: LIMIT 0 moves no data but still fails on a
        # missing table or grant at startup. delta_table is regex-validated by Settings, so the
        # f-string interpolation is safe.
        with self._connection().cursor() as cur:
            cur.execute(f"SELECT 1 FROM {self.cfg.delta_table} LIMIT 0")
        logger.info("Delta Table writer ready", table=self.cfg.delta_table, auth=self.cfg.auth.method)

    async def write_batch(self, store: Store, limit: int) -> Records:
        r = await store.read(limit)
        if r.cursor is not None:
            await asyncio.to_thread(self._insert, r)
        return r

    def _insert(self, r: Records) -> None:
        query, params = self.build_insert(self.cfg.delta_table, r.rows)
        try:
            with self._connection().cursor() as cur:
                cur.execute(query, params)
        except Exception:
            self._reset()                               # drop a possibly-stale connection; retry reconnects
            raise
        logger.info("Uploaded to Delta table", rows=r.n_rows, backlog=r.backlog,
                    table=self.cfg.delta_table)

    def _connection(self):
        # use_inline_params lifts the connector's 255-native-parameter-marker cap so the default
        # batch_size (1000 rows x 4 params) fits in one atomic INSERT; "silent" enables inlining
        # (same as True) minus the per-query usage warning the connector emits for plain True.
        if self._con is None:
            a = self.cfg.auth
            if a.method == "access_token":
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.access_token is None:
                    raise RuntimeError("auth.method='access_token' but no access_token configured")
                self._con = sql.connect(server_hostname=self.cfg.server_hostname, http_path=self.cfg.http_path,
                                        access_token=a.access_token.get_secret_value(),
                                        use_inline_params="silent", _user_agent_entry=USER_AGENT)
            else:
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.client_id is None or a.client_secret is None:
                    raise RuntimeError("auth.method='oauth' but client_id/client_secret not configured")
                conf = Config(host=f"https://{self.cfg.server_hostname}",
                              client_id=a.client_id.get_secret_value(),
                              client_secret=a.client_secret.get_secret_value())
                self._con = sql.connect(server_hostname=self.cfg.server_hostname, http_path=self.cfg.http_path,
                                        credentials_provider=oauth_service_principal(conf),
                                        use_inline_params="silent", _user_agent_entry=USER_AGENT)
        return self._con

    def _reset(self) -> None:
        con, self._con = self._con, None
        if con is not None:
            try:
                con.close()
            except Exception:
                logger.warning("Failed to close stale Delta connection")

    async def teardown(self) -> None:
        # Best-effort close: on shutdown this may race a lingering _insert running on a
        # cancelled drain's worker thread, so _reset swallows close() errors rather than locks.
        await asyncio.to_thread(self._reset)
