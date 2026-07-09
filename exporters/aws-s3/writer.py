import asyncio
import os
import tempfile
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from kelvin.logs import logger

from settings import S3
from store import FileRecords, Store

# ClientError codes that mean the deployment is misconfigured (never self-heal):
# bad/insufficient credentials or a bucket that doesn't exist.
_CONFIG_ERROR_CODES = {"403", "AccessDenied", "404", "NoSuchBucket",
                       "InvalidAccessKeyId", "SignatureDoesNotMatch"}


class S3Writer:
    """File sink: stream a batch from the buffer to a temp file, then upload it to S3.

    boto3 is synchronous, so the blocking calls run in asyncio.to_thread. The client is
    built once and reused; a failed upload rebuilds it and raises so the drain retries the
    whole batch (the buffer isn't trimmed until the object lands).
    """

    def __init__(self, cfg: S3, fmt: str):
        self.cfg = cfg
        self.fmt = fmt              # "parquet"|"csv"|"json"; drives store.read_to_file
        self._client = None

    def _connection(self):
        if self._client is None:
            a = self.cfg.auth
            kwargs = {"region_name": self.cfg.region}
            if a.access_key_id and a.secret_access_key:
                kwargs["aws_access_key_id"] = a.access_key_id.get_secret_value()
                kwargs["aws_secret_access_key"] = a.secret_access_key.get_secret_value()
            # No keys -> boto3 uses the AWS default credential chain (IAM role, env, profile).
            self._client = boto3.client("s3", **kwargs)
        return self._client

    async def setup(self) -> None:
        await asyncio.to_thread(self._validate)

    def _validate(self) -> None:
        # Failure policy: deterministic config errors (403/AccessDenied, 404/NoSuchBucket, bad
        # keys) raise, so the deployment fails fast and visibly. Transient failures (endpoint
        # unreachable, timeouts, 5xx) and anything unclassified only warn: the persistent buffer
        # keeps accepting data and the drain's retry owns connectivity.
        try:
            self._connection().head_bucket(Bucket=self.cfg.bucket)   # proves bucket + auth + network
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if code in _CONFIG_ERROR_CODES or status in (401, 403, 404):
                raise                                    # misconfiguration: crash the deployment
            logger.warning("S3 bucket check failed; buffering until the drain retries",
                           bucket=self.cfg.bucket, error=str(e), error_type=type(e).__name__)
            return
        except Exception as e:      # EndpointConnectionError, timeouts, anything unclassified
            logger.warning("S3 unreachable at setup; buffering until the drain retries",
                           bucket=self.cfg.bucket, error=str(e), error_type=type(e).__name__)
            return
        logger.info("S3 writer ready", bucket=self.cfg.bucket, region=self.cfg.region, format=self.fmt)

    async def write_batch(self, store: Store, limit: int) -> FileRecords:
        # File sink: COPY the batch to a temp file (streamed to disk, never materialized in
        # Python), upload it streaming from disk, then always delete the temp file.
        fd, path = tempfile.mkstemp(prefix="kelvin-", suffix=f".{self.fmt}")
        os.close(fd)
        try:
            r = await store.read_to_file(path, self.fmt, limit)
            if r.cursor is not None:
                await asyncio.to_thread(self._upload, path, r)
            return r
        finally:
            if os.path.exists(path):
                os.remove(path)

    def _upload(self, path: str, r: FileRecords) -> None:
        # Timestamp generated at upload time: keys never collide across deployments or after a
        # volume loss. The trade-off is at-least-once delivery; a retried upload writes the
        # same batch under a second name (see README's delivery-semantics note).
        key = f"batch-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{r.cursor}.{self.fmt}"
        if self.cfg.prefix:
            key = f"{self.cfg.prefix.strip('/')}/{key}"
        try:
            self._connection().upload_file(path, self.cfg.bucket, key)   # streams from disk
        except Exception:
            self._reset()                               # drop a possibly-stale client; retry rebuilds
            raise
        logger.info("Uploaded to S3", rows=r.n_rows, backlog=r.backlog,
                    bucket=self.cfg.bucket, key=key)

    def _reset(self) -> None:
        self._client = None

    async def teardown(self) -> None:
        # Best-effort: boto3 clients pool HTTP connections but expose no public close, so
        # dropping the reference is all we can do; nothing here can race a worker thread.
        await asyncio.to_thread(self._reset)
