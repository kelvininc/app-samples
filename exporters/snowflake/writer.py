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


def _private_key_der(pem: str, passphrase: Optional[str]) -> bytes:
    """Load a PEM RSA private key and return it as PKCS8 DER bytes (what the connector wants)."""
    key = serialization.load_pem_private_key(
        pem.encode(), password=passphrase.encode() if passphrase else None
    )
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

    def __init__(self, cfg: Snowflake):
        self.cfg = cfg
        self._con = None
        self._lock = threading.Lock()

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
        """Probe connectivity so deterministic config errors fail fast at connect time.
        Auth failures, a missing database/schema/table, permission errors, and bad key
        material won't fix themselves: raise. A transient or unknown failure (network,
        service hiccup) only warns; the drain loop retries with backoff and the buffer
        holds the data meanwhile."""
        try:
            await asyncio.to_thread(self._validate)
        except snowflake.connector.errors.OperationalError as e:
            # OperationalError subclasses DatabaseError; this clause must come first so
            # network-ish failures stay tolerated instead of falling into the fatal branch.
            await asyncio.to_thread(self._reset)        # drop the possibly-stale connection; drain reconnects
            logger.warning("Snowflake probe failed (transient); drain will retry", error=str(e))
        except (snowflake.connector.errors.DatabaseError,   # auth failure, no permission; covers
                                                            # ProgrammingError (its subclass), which the
                                                            # probe gets for a missing database/schema/table
                ValueError):                                # malformed private key / wrong passphrase
            raise
        except Exception as e:
            await asyncio.to_thread(self._reset)        # drop the possibly-stale connection; drain reconnects
            logger.warning("Snowflake probe failed (unknown); drain will retry", error=str(e))

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
            await asyncio.to_thread(self._insert, r.rows)
        return r

    def _insert(self, rows: list[dict]) -> None:
        query, params = self.build_insert(rows)
        try:
            with self._lock:                            # held for the whole statement (see class docstring)
                with self._connection().cursor() as cur:
                    cur.execute(query, params)
        except Exception:
            self._reset()                               # drop a possibly-stale connection; retry reconnects
            raise
        logger.info("Uploaded to Snowflake", count=len(rows), table=self._fqn)

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
