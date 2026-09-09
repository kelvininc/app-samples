import asyncio
import json
import math
import threading
from typing import Optional

import snowflake.connector
from cryptography.hazmat.primitives import serialization
from kelvin.logs import logger

from settings import Snowflake
from store import Records, Store

# Bind parameters with ? (like the Delta exporter), not the connector's default %s pyformat.
snowflake.connector.paramstyle = "qmark"


def _payload_json(payload: object) -> str:
    """JSON-encode a scalar payload for PARSE_JSON(). Non-finite floats (NaN/Inf) become null:
    json.dumps would otherwise emit literal NaN/Infinity, which PARSE_JSON rejects, failing the
    whole batch and stalling the buffer behind one poison row."""
    if isinstance(payload, float) and not math.isfinite(payload):
        return "null"
    return json.dumps(payload)


class KeyMaterialError(Exception):
    """Deterministic failure loading the configured RSA private key: malformed PEM, an
    encrypted key supplied without a passphrase, or a wrong passphrase. Bad key material
    won't fix itself, so setup() treats this as fatal and fails the deploy on the first
    probe attempt instead of retrying."""


def _fatal_probe_error(e: Exception) -> bool:
    """Is this probe failure a deterministic config error the drain can't retry away?

    OperationalError subclasses DatabaseError and covers network-ish, retryable failures
    (including an unresolvable host from a wrong account), so check it first and treat it
    as non-fatal. A DatabaseError that isn't Operational (rejected auth, missing
    database/schema/table, no permission), bad key material, or other malformed input
    surfaced as ValueError is fatal: fail the deploy on the first attempt."""
    if isinstance(e, snowflake.connector.errors.OperationalError):
        return False
    return isinstance(e, (snowflake.connector.errors.DatabaseError, KeyMaterialError, ValueError))


def _private_key_der(pem: str, passphrase: Optional[str]) -> bytes:
    """Load a PEM RSA private key and return it as PKCS8 DER bytes (what the connector wants)."""
    try:
        key = serialization.load_pem_private_key(
            pem.encode(), password=passphrase.encode() if passphrase else None
        )
    except (TypeError, ValueError) as e:
        # cryptography raises TypeError when an encrypted key is loaded without a passphrase
        # (or a passphrase is given for an unencrypted key), and ValueError for malformed PEM
        # or a wrong passphrase. Both are deterministic config errors: normalize to a single
        # fatal type so the startup probe fails fast rather than burning every attempt.
        raise KeyMaterialError(str(e)) from e
    return key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


class SnowflakeWriter:
    """Record sink: read a batch from the buffer and INSERT it into the Snowflake table.

    snowflake-connector is synchronous, so the blocking calls run in asyncio.to_thread. The
    connection is reused across ticks and rebuilt after a failed write.

    Every ``self._con`` op holds a lock; the same teardown/in-flight discipline Store
    documents: a cancelled drain's to_thread insert keeps running on a worker thread, so
    ``teardown()``'s close must wait it out instead of closing the connection under it.
    """

    fmt = None      # record sink: wants dict rows, not a file

    def __init__(self, cfg: Snowflake, probe_attempts: int = 5, probe_delay: float = 2.0):
        self.cfg = cfg
        self._con = None
        self._lock = threading.Lock()
        # Bounded startup probe: retry connectivity a few times, then fail the deploy (see setup()).
        # Floor at 1 so the probe always runs at least once (probe_attempts=0 can't underrun the loop).
        self._probe_attempts = max(1, probe_attempts)
        self._probe_delay = probe_delay

    @property
    def _fqn(self) -> str:
        """Fully-qualified table name; each part is a validated identifier (no injection)."""
        return f"{self.cfg.database}.{self.cfg.schema_}.{self.cfg.table}"

    def build_insert(self, rows: list[dict]) -> tuple[str, list]:
        """One atomic multi-row INSERT and its flat parameter list. A single statement (not
        executemany) so the batch commits whole or not at all; recovery is at-least-once (a
        committed-but-unacked batch re-sends and, since the table enforces no key, duplicates).
        Values are bound; PARSE_JSON turns the scalar-JSON payload into a VARIANT."""
        if not rows:
            raise ValueError("build_insert requires at least one row")
        values = ", ".join(["(?, ?, ?, ?)"] * len(rows))
        query = (
            f"INSERT INTO {self._fqn} (timestamp, asset, datastream, payload)\n"
            f"SELECT column1, column2, column3, PARSE_JSON(column4)\n"
            f"FROM VALUES {values}"
        )
        params: list = []
        for r in rows:
            params += [r["timestamp"], r["asset"], r["datastream"], _payload_json(r["payload"])]
        return query, params

    async def setup(self) -> None:
        """Bounded startup probe so a permanently-bad config fails the deploy instead of
        retrying forever. Deterministic config errors (auth failure, a missing
        database/schema/table, permission errors, bad key material) won't fix themselves:
        raise on the first attempt. A transient-looking failure (network, service hiccup)
        is retried a small number of times; if the probe never once connects it gives up
        and raises. That last part matters for a typo'd or nonexistent ``account``: it
        surfaces as an unresolvable-host OperationalError (not a DatabaseError), so without
        the bounded cap the drain loop would treat it as transient and retry forever,
        never delivering. Genuinely transient mid-run failures still ride the drain loop's
        retry/backoff, with the buffer holding the data meanwhile."""
        for attempt in range(1, self._probe_attempts + 1):
            try:
                await asyncio.to_thread(self._validate)
                return                                      # connected: app starts
            except Exception as e:
                if _fatal_probe_error(e):
                    raise                                   # deterministic config error: fail the deploy now
                await asyncio.to_thread(self._reset)        # drop the possibly-stale connection
                if attempt == self._probe_attempts:
                    # Never connected across the whole probe: fatal, so a bad deployment
                    # fails loudly instead of the drain retrying forever with data that can
                    # never be delivered (e.g. an unresolvable host from a wrong account).
                    logger.error("Snowflake probe failed after all attempts; failing startup",
                                 attempts=self._probe_attempts,
                                 error=str(e), error_type=type(e).__name__)
                    raise
                logger.warning("Snowflake probe failed; retrying",
                               attempt=f"{attempt}/{self._probe_attempts}",
                               error=str(e), error_type=type(e).__name__)
                await asyncio.sleep(self._probe_delay)

    def _validate(self) -> None:
        # Probe the table itself, not just SELECT 1: LIMIT 0 moves no data but still fails on a
        # missing database/schema/table or grant at startup. Each part of the FQN is
        # regex-validated by Settings, so the f-string interpolation is safe.
        with self._lock:
            with self._connection().cursor() as cur:
                cur.execute(f"SELECT 1 FROM {self._fqn} LIMIT 0")
        logger.info("Snowflake writer ready", table=self._fqn, auth=self.cfg.auth.method)

    async def write_batch(self, store: Store, limit: int) -> Records:
        r = await store.read(limit)
        if r.cursor is not None:
            await asyncio.to_thread(self._insert, r)
        return r

    def _insert(self, r: Records) -> None:
        query, params = self.build_insert(r.rows)
        try:
            with self._lock:                            # held for the whole statement (see class docstring)
                with self._connection().cursor() as cur:
                    cur.execute(query, params)
        except Exception:
            self._reset()                               # drop a possibly-stale connection; retry reconnects
            raise
        logger.info("Uploaded to Snowflake", rows=r.n_rows, backlog=r.backlog, table=self._fqn)

    def _connection(self):
        # Callers hold self._lock; this only builds/returns the shared connection.
        if self._con is None:
            a = self.cfg.auth
            kwargs: dict = dict(
                account=self.cfg.account, user=self.cfg.user, warehouse=self.cfg.warehouse,
                database=self.cfg.database, schema=self.cfg.schema_,
            )
            if a.method == "password":
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.password is None:
                    raise RuntimeError("auth.method='password' but no password configured")
                kwargs["password"] = a.password.get_secret_value()
            else:
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.private_key is None:
                    raise RuntimeError("auth.method='key_pair' but no private_key configured")
                passphrase = a.private_key_passphrase.get_secret_value() if a.private_key_passphrase else None
                kwargs["private_key"] = _private_key_der(a.private_key.get_secret_value(), passphrase)
            self._con = snowflake.connector.connect(**kwargs)
        return self._con

    def _reset(self) -> None:
        # Taking the lock makes close() wait out an in-flight _insert on a worker thread
        # (same use-after-close guard as Store._teardown).
        with self._lock:
            con, self._con = self._con, None
            if con is not None:
                try:
                    con.close()
                except Exception:
                    logger.warning("Failed to close stale Snowflake connection")

    async def teardown(self) -> None:
        await asyncio.to_thread(self._reset)
