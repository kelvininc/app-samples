import os
import tempfile
from datetime import datetime, timezone

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.storage.filedatalake.aio import DataLakeServiceClient
from kelvin.logs import logger

from settings import ADLS
from store import FileRecords, Store


class ADLSWriter:
    """File sink: stream a batch from the buffer to a temp file, then upload it to ADLS.

    The azure SDK is natively async, so its calls are awaited directly (no asyncio.to_thread).
    The service client is built once and reused; a failed upload drops it and raises so the
    drain retries the whole batch (the buffer isn't trimmed until the object lands).

    Auth: an explicit account_key, or; when none is set; DefaultAzureCredential, which uses
    the cluster's managed identity (the Azure analog of the AWS default credential chain), so
    production can run secretless.
    """

    def __init__(self, cfg: ADLS, fmt: str):
        self.cfg = cfg
        self.fmt = fmt              # "parquet"|"csv"|"json"; drives store.read_to_file
        self._service_client = None
        self._credential = None     # the DefaultAzureCredential, if we created one (must be closed)

    def _connection(self) -> DataLakeServiceClient:
        if self._service_client is None:
            a = self.cfg.auth
            if a.account_key:
                credential = a.account_key.get_secret_value()
            else:
                credential = self._credential = DefaultAzureCredential()   # managed identity
            self._service_client = DataLakeServiceClient(
                account_url=f"https://{self.cfg.account_name}.dfs.core.windows.net",
                credential=credential,
            )
        return self._service_client

    async def setup(self) -> None:
        # Connectivity check: prove the container exists. Deterministic config errors
        # (missing container, bad credentials) fail fast; transient/unknown failures
        # (DNS, timeout) only warn; the drain loop retries every upload anyway.
        fs = self._connection().get_file_system_client(self.cfg.container)
        try:
            await fs.get_file_system_properties()
        except (ClientAuthenticationError, ResourceNotFoundError):
            raise                                       # config error: container/credentials
        except HttpResponseError as e:
            if e.status_code in (401, 403, 404):        # auth/not-found via generic HTTP error
                raise
            logger.warning("ADLS connectivity check failed; continuing",
                           error=str(e), error_type=type(e).__name__)
        except Exception as e:                          # transient/unknown: uploads will retry
            logger.warning("ADLS connectivity check failed; continuing",
                           error=str(e), error_type=type(e).__name__)
        else:
            logger.info("ADLS writer ready", account=self.cfg.account_name,
                        container=self.cfg.container, format=self.fmt)

    async def write_batch(self, store: Store, limit: int) -> FileRecords:
        # File sink: COPY the batch to a temp file (streamed to disk, never materialized in
        # Python), upload it streaming from disk, then always delete the temp file.
        fd, path = tempfile.mkstemp(prefix="kelvin-", suffix=f".{self.fmt}")
        os.close(fd)
        try:
            r = await store.read_to_file(path, self.fmt, limit)
            if r.cursor is not None:
                await self._upload(path, r)
            return r
        finally:
            if os.path.exists(path):
                os.remove(path)

    async def _upload(self, path: str, r: FileRecords) -> None:
        # Timestamp generated at upload time: a retried upload gets a fresh name, so the
        # same batch can land under two names (at-least-once; consumers dedupe).
        name = f"batch-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{r.cursor}.{self.fmt}"
        size = os.path.getsize(path)
        try:
            fs = self._connection().get_file_system_client(self.cfg.container)
            file_client = fs.get_file_client(name)
            # Stream from disk in chunks; do NOT read the whole file into memory.
            with open(path, "rb") as f:
                await file_client.upload_data(f, length=size, overwrite=True)
        except Exception:
            await self._reset()                 # drop a possibly-stale client; retry rebuilds
            raise
        logger.info("Uploaded to ADLS", rows=r.n_rows, backlog=r.backlog,
                    container=self.cfg.container, name=name)

    async def _reset(self) -> None:
        client, self._service_client = self._service_client, None
        credential, self._credential = self._credential, None
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.warning("Failed to close stale ADLS client")
        if credential is not None:                  # close the managed-identity token client too
            try:
                await credential.close()
            except Exception:
                logger.warning("Failed to close stale ADLS credential")

    async def teardown(self) -> None:
        # Real close (the azure async client owns an aiohttp session). Best-effort: on
        # shutdown this may race a lingering upload, so _reset swallows close() errors.
        await self._reset()
