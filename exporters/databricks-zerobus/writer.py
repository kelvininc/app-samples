import math
from datetime import timezone
from typing import Optional

from kelvin.logs import logger
from zerobus.sdk.aio import ZerobusSdk
from zerobus.sdk.aio.zerobus_sdk import NonRetriableException
from zerobus.sdk.shared import RecordType, StreamConfigurationOptions, TableProperties

from settings import Databricks
from store import Records, Store


def _with_scheme(hostname: str) -> str:
    """Ensure a hostname is a full https URL.

    Config holds bare hostnames (e.g. dbc-xxxx.cloud.databricks.com), matching the other
    Databricks exporters, while the Zerobus SDK expects full URLs. An explicit
    http:// or https:// scheme is left untouched. The inputs are the validated, non-empty
    `server_hostname`/`zerobus_endpoint`, so this takes and returns a plain str.
    """
    if not hostname:
        return hostname
    if hostname.startswith(("http://", "https://")):
        return hostname
    return f"https://{hostname}"


def _payload_value(payload):
    """Coerce a scalar payload to a JSON-serializable value for a Zerobus record.

    Non-finite floats (NaN/Inf) become None: the record is serialized to JSON for ingestion,
    and json.dumps would otherwise emit literal NaN/Infinity, which Zerobus rejects, failing
    the whole batch and stalling the buffer behind one poison row.
    """
    if isinstance(payload, float) and not math.isfinite(payload):
        return None
    return payload


class ZerobusWriter:
    """Record sink: read a batch from the buffer and ingest it over a Zerobus gRPC stream.

    The zerobus aio SDK is natively async, so its calls are awaited directly (no
    asyncio.to_thread). A single long-lived stream is reused across batches; a failed
    batch closes the stream and re-raises, so the next attempt reopens a fresh one.

    Recovery is at-least-once, not exactly-once: write_batch waits for the server to
    ACKNOWLEDGE the batch's final offset (wait_for_offset) before returning, so the buffer is
    only trimmed once data is durable, but a committed-but-unacked batch is re-sent on the next
    tick and, since the table enforces no key, duplicates; downstream should tolerate that.
    """

    fmt = None      # record sink: wants dict rows, not a file

    def __init__(self, cfg: Databricks):
        self.cfg = cfg
        self._sdk: Optional[ZerobusSdk] = None
        self._stream = None

    @staticmethod
    def build_record(row: dict) -> dict:
        """Build one JSON-serializable Zerobus record from a buffered row.

        The timestamp is serialized to an ISO-8601 string (Zerobus serializes the record to
        JSON, which can't carry a datetime). The buffer stores it as naive UTC, so it's marked
        with an explicit +00:00 UTC offset here instead of emitting an ambiguous naive string.
        A non-finite float payload becomes None.
        """
        return {
            "timestamp": row["timestamp"].replace(tzinfo=timezone.utc).isoformat(),
            "asset": row["asset"],
            "datastream": row["datastream"],
            "payload": _payload_value(row["payload"]),
        }

    async def setup(self) -> None:
        # ZerobusSdk.__init__ validates nothing (even malformed URLs), so opening the stream
        # eagerly is the only way to surface problems at deploy. The stream is kept and reused
        # by the first drain tick; no probe-and-close.
        endpoint = _with_scheme(self.cfg.zerobus_endpoint)
        workspace_url = _with_scheme(self.cfg.server_hostname)
        self._sdk = ZerobusSdk(endpoint, workspace_url)
        # Failure policy (same convention as the sibling exporters): deterministic config errors
        # (bad OAuth client_id/secret, nonexistent/forbidden table; the SDK's NonRetriable class)
        # raise, so the deployment fails fast and visibly. Transient failures and anything
        # unclassified only warn: the persistent buffer keeps accepting data, and the writer's
        # close-and-reopen-on-failure machinery owns connectivity from here on.
        try:
            await self._ensure_stream()
        except NonRetriableException:
            raise                                   # misconfiguration: crash the deployment
        except Exception as e:
            logger.warning("Zerobus unreachable at setup; buffering and retrying",
                           table=self.cfg.delta_table, endpoint=endpoint,
                           error=str(e), error_type=type(e).__name__)
            return
        logger.info("Zerobus writer ready", table=self.cfg.delta_table, endpoint=endpoint)

    async def write_batch(self, store: Store, limit: int) -> Records:
        r = await store.read(limit)
        if r.cursor is not None:
            stream = await self._ensure_stream()
            try:
                # ingest_records_offset returns the batch's final offset; flush pushes the
                # pending records and wait_for_offset blocks until the server ACKNOWLEDGES that
                # offset. Only then does write_batch return, so the drain trims the buffer strictly
                # after durability (at-least-once), never after a mere send.
                last_offset = await stream.ingest_records_offset(
                    [self.build_record(row) for row in r.rows]
                )
                await stream.flush()
                # Guaranteed non-None: cursor is not None => batch non-empty => real offset.
                # Explicit raise survives python -O and flows into the except below (close + reraise).
                if last_offset is None:
                    raise RuntimeError("non-empty batch returned no offset")
                await stream.wait_for_offset(last_offset)
            except Exception:
                await self._close_stream()      # drop the stream so the next attempt reopens clean
                raise
            logger.info("Published to Zerobus", rows=r.n_rows, backlog=r.backlog,
                        table=self.cfg.delta_table)
        return r

    async def _ensure_stream(self):
        if self._stream is None:
            a = self.cfg.auth
            # Guaranteed by setup() running before any write; explicit raise survives python -O.
            if self._sdk is None:
                raise RuntimeError("Zerobus writer used before setup()")
            # Guaranteed by Settings' require-both validator; explicit raise survives python -O.
            if a.client_id is None or a.client_secret is None:
                raise RuntimeError("auth requires client_id and client_secret")
            options = StreamConfigurationOptions(record_type=RecordType.JSON)
            self._stream = await self._sdk.create_stream(
                a.client_id.get_secret_value(),
                a.client_secret.get_secret_value(),
                TableProperties(self.cfg.delta_table),
                options,
            )
            logger.info("Opened Zerobus stream", table=self.cfg.delta_table)
        return self._stream

    async def _close_stream(self) -> None:
        if self._stream is not None:
            stream, self._stream = self._stream, None
            try:
                await stream.close()
            except Exception:
                logger.warning("Failed to close stale Zerobus stream")

    async def teardown(self) -> None:
        await self._close_stream()
