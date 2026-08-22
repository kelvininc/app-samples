import asyncio
import json
import math

from databricks import sql
# oauth_service_principal is the documented Databricks helper but isn't re-exported in the stubs.
from databricks.sdk.core import Config, oauth_service_principal  # pyright: ignore[reportPrivateImportUsage]
from databricks.sql.exc import RequestError, ServerOperationError
from kelvin.logs import logger

from settings import Databricks
from store import Records, Store

USER_AGENT = "kelvin.ai"

# HTTP statuses that mean the request was understood but the credentials/grants were
# refused: retrying with the same config never clears them.
_AUTH_HTTP_CODES = {"401", "403"}


class OAuthCredentialError(RuntimeError):
    """A rejected OAuth service-principal credential. The databricks-sdk M2M token fetch
    raises a bare ValueError ("invalid_client: ...") from the OIDC token exchange when the
    client_id/secret is wrong; _connection wraps only that connect/token-path ValueError in
    this type so setup() treats it as a deterministic fatal config error (fail the deploy)
    instead of a transient one it retries forever."""


def _fatal_setup_error(e: Exception) -> bool:
    """Deterministic config errors the drain can't retry away, classified by the
    connector's and SDK's structured error *types/attributes* rather than by scanning
    str(e) for "401"/"403"/etc. (which both misfired -- e.g. a "403" that's really a row
    count or part of a table name -- and missed wrapped errors).

    - ServerOperationError: the probe reached the warehouse and the *operation* was
      rejected (missing table, missing grant, syntax error). Always deterministic.
    - RequestError exposes the HTTP status in .context["http-code"]; 401/403 there mean
      the access token itself was refused (the databricks-sql / PAT path).
    - OAuthCredentialError: a rejected OAuth service-principal credential. _connection wraps
      the bare ValueError the databricks-sdk raises from the M2M token fetch in this type,
      so a bad client_id/secret fails fast (the oauth path)."""
    if isinstance(e, (ServerOperationError, OAuthCredentialError)):
        return True
    if isinstance(e, RequestError) and isinstance(e.context, dict):
        # http-code may arrive as an int or a stringified code; compare as strings so both
        # forms hit the same check, and any non-auth/missing/non-digit code falls through.
        return str(e.context.get("http-code")) in _AUTH_HTTP_CODES
    return False


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
                try:
                    # oauth_service_principal fetches the OIDC metadata and sql.connect opens
                    # the session, which acquires the M2M token. A rejected client_id/secret
                    # surfaces here (and only here) as a bare ValueError ("invalid_client: ...")
                    # from the databricks-sdk token fetch; re-raise it as OAuthCredentialError so
                    # setup() fails fast on a credential that will never start working. Scoped to
                    # this connect/token call so unrelated ValueErrors stay transient.
                    self._con = sql.connect(server_hostname=self.cfg.server_hostname, http_path=self.cfg.http_path,
                                            credentials_provider=oauth_service_principal(conf),
                                            use_inline_params="silent", _user_agent_entry=USER_AGENT)
                except ValueError as e:
                    raise OAuthCredentialError(str(e)) from e
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
