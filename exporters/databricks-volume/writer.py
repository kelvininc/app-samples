import asyncio
import os
import tempfile
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import BadRequest, NotFound, PermissionDenied, Unauthenticated
from kelvin.logs import logger

import job
from settings import Databricks
from store import FileRecords, Store

PRODUCT = "kelvin-databricks-volume"
PRODUCT_VERSION = "2.0.0"


class VolumeWriter:
    """File sink: stream a batch from the buffer to a temp file, then upload it to a
    Databricks Unity Catalog Volume. A file-arrival-triggered ingestion job (created in
    setup) loads each uploaded file into the Delta table.

    databricks-sdk is synchronous, so the blocking calls run in asyncio.to_thread. The
    WorkspaceClient is built once in setup and reused; a failed upload resets it and
    re-raises so the drain retries the whole batch (the buffer isn't trimmed until the
    file lands). Recovery is at-least-once: a re-sent file the ingestion job already read
    can re-ingest, so downstream (the Delta table) should tolerate duplicate rows.
    """

    def __init__(self, cfg: Databricks, fmt: str):
        self.cfg = cfg
        self.fmt = fmt              # "parquet"|"csv"; drives store.read_to_file
        self._client = None

    def _connection(self) -> WorkspaceClient:
        if self._client is None:
            a = self.cfg.auth
            if a.method == "access_token":
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.access_token is None:
                    raise RuntimeError("auth.method='access_token' but no access_token configured")
                self._client = WorkspaceClient(
                    host=f"https://{self.cfg.server_hostname}",
                    token=a.access_token.get_secret_value(),
                    product=PRODUCT,
                    product_version=PRODUCT_VERSION,
                )
            else:
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.client_id is None or a.client_secret is None:
                    raise RuntimeError("auth.method='oauth' but client_id/client_secret not configured")
                self._client = WorkspaceClient(
                    host=f"https://{self.cfg.server_hostname}",
                    client_id=a.client_id.get_secret_value(),
                    client_secret=a.client_secret.get_secret_value(),
                    product=PRODUCT,
                    product_version=PRODUCT_VERSION,
                )
        return self._client

    def _volume_data_path(self, cursor: int) -> str:
        """`catalog.schema.volume` + cursor -> /Volumes/<cat>/<schema>/<vol>/data/batch-<utc>-<cursor>.<fmt>.
        The upload-time UTC stamp keeps names unique across deployments and buffer resets
        (the cursor alone restarts at 1 with a fresh buffer). A retried upload of the same
        batch gets a fresh stamp; the at-least-once duplication documented in the README."""
        catalog, schema, name = self.cfg.uc_volume.split(".")
        stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
        return f"/Volumes/{catalog}/{schema}/{name}/data/batch-{stamp}-{cursor}.{self.fmt}"

    async def setup(self) -> None:
        await asyncio.to_thread(self._setup)            # fail fast on bad host/credentials

    def _setup(self) -> None:
        w = self._connection()
        try:
            w.current_user.me()                         # round-trips: proves host + auth + network
            self._ensure_ingestion_job(w)
        except (BadRequest, NotFound, PermissionDenied, Unauthenticated):
            raise    # deterministic config error (bad credentials/host/ids/grants): retrying can't fix it
        except Exception as e:
            # Transient or unclassified (network blip, 5xx): start anyway; the drain loop
            # retries uploads, and the ingestion job is re-ensured on the next restart.
            logger.warning("Databricks connectivity check failed; continuing", error=str(e))
            return
        logger.info("Volume writer ready", volume=self.cfg.uc_volume,
                    table=self.cfg.delta_table, auth=self.cfg.auth.method, format=self.fmt)

    def _ensure_ingestion_job(self, w: WorkspaceClient) -> None:
        if self.cfg.job.warehouse_id:
            job.create_job_copy_into(
                w, volume=self.cfg.uc_volume, table=self.cfg.delta_table,
                warehouse_id=self.cfg.job.warehouse_id, fmt=self.fmt,
            )
        elif self.cfg.job.cluster_id:
            job.create_job_autoloader(
                w, volume=self.cfg.uc_volume, table=self.cfg.delta_table,
                cluster_id=self.cfg.job.cluster_id, fmt=self.fmt,
            )
        else:
            logger.info("No warehouse_id or cluster_id set; skipping ingestion-job creation")

    async def write_batch(self, store: Store, limit: int) -> FileRecords:
        # File sink: COPY the batch to a temp file (streamed to disk, never materialized in
        # Python), upload it streaming from disk, then always delete the temp file.
        fd, path = tempfile.mkstemp(prefix="kelvin-", suffix=f".{self.fmt}")
        os.close(fd)
        try:
            r = await store.read_to_file(path, self.fmt, limit)
            if r.cursor is not None:
                await asyncio.to_thread(self._upload, path, r.cursor)
            return r
        finally:
            if os.path.exists(path):
                os.remove(path)

    def _upload(self, path: str, cursor: int) -> None:
        volume_path = self._volume_data_path(cursor)
        try:
            with open(path, "rb") as f:
                self._connection().files.upload(volume_path, f, overwrite=True)   # streams from disk
        except Exception:
            self._reset()                               # drop a possibly-stale client; retry rebuilds
            raise
        logger.info("Uploaded to Volume", volume_path=volume_path)

    def _reset(self) -> None:
        self._client = None

    async def teardown(self) -> None:
        # Best-effort: the WorkspaceClient holds no connection to close, so dropping the
        # reference is all we can do; nothing here can race a worker thread.
        await asyncio.to_thread(self._reset)
