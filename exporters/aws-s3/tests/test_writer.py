"""Unit tests for S3Writer: client building, key layout, and temp-file lifecycle (no cloud)."""
import os
import re
from datetime import datetime

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError, EndpointConnectionError

import writer as writer_mod
from settings import Settings
from store import Store
from writer import S3Writer


def _cfg(**over):
    return Settings(s3={"region": "us-east-1", "bucket": "telemetry", **over}).s3


class _FakeClient:
    def __init__(self) -> None:
        self.uploads: list[tuple] = []
        self.fail = False
        self.head_error: Exception | None = None       # raised by head_bucket when set

    def head_bucket(self, Bucket):  # noqa: N803 (boto3 kwarg name)
        if self.head_error is not None:
            raise self.head_error

    def upload_file(self, path, bucket, key):
        if self.fail:
            raise RuntimeError("network down")
        self.uploads.append((bucket, key, os.path.exists(path)))   # source present at upload time?


class _FakeBoto:
    def __init__(self) -> None:
        self.kwargs = None
        self.obj = _FakeClient()

    def client(self, name, **kw):
        self.kwargs = kw
        return self.obj


@pytest_asyncio.fixture
async def seeded() -> Store:
    s = Store(":memory:")
    await s.setup()
    await s.append(datetime(2026, 1, 1), "pump-1", "temperature", 42.5)
    return s


class TestConnection:
    """boto3 client construction (synchronous)."""

    def test_unwraps_secret_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit keys are passed to boto3 unwrapped from SecretStr."""
        fake = _FakeBoto()
        monkeypatch.setattr(writer_mod, "boto3", fake)
        w = S3Writer(_cfg(auth={"access_key_id": "AKIA", "secret_access_key": "shhh"}), "parquet")
        w._connection()
        assert fake.kwargs["aws_access_key_id"] == "AKIA"
        assert fake.kwargs["aws_secret_access_key"] == "shhh"
        assert fake.kwargs["region_name"] == "us-east-1"

    def test_default_chain_omits_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no keys, boto3 is called with region only (AWS default credential chain)."""
        fake = _FakeBoto()
        monkeypatch.setattr(writer_mod, "boto3", fake)
        S3Writer(_cfg(), "parquet")._connection()
        assert "aws_access_key_id" not in fake.kwargs and fake.kwargs["region_name"] == "us-east-1"


@pytest.mark.asyncio
class TestSetup:
    """Failure policy for the startup head_bucket check: config errors crash,
    transient/unknown errors warn and defer to the drain's retry."""

    def _writer(self, monkeypatch: pytest.MonkeyPatch, head_error: Exception) -> S3Writer:
        fake = _FakeBoto()
        fake.obj.head_error = head_error
        monkeypatch.setattr(writer_mod, "boto3", fake)
        return S3Writer(_cfg(), "parquet")

    @pytest.mark.parametrize("code,status", [("403", 403), ("AccessDenied", 403), ("404", 404),
                                             ("NoSuchBucket", 404), ("301", 301), ("PermanentRedirect", 301)])
    async def test_config_error_fails_fast(self, monkeypatch: pytest.MonkeyPatch, code: str, status: int) -> None:
        """A deterministic misconfiguration (bad credentials, missing bucket, or a bucket in a
        different region -> 301/PermanentRedirect) raises instead of retrying forever."""
        err = ClientError({"Error": {"Code": code, "Message": "no"},
                           "ResponseMetadata": {"HTTPStatusCode": status}}, "HeadBucket")
        with pytest.raises(ClientError):
            await self._writer(monkeypatch, err).setup()

    async def test_transient_5xx_warns_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A server-side 5xx is transient: setup logs a warning instead of crashing."""
        err = ClientError({"Error": {"Code": "InternalError", "Message": "oops"},
                           "ResponseMetadata": {"HTTPStatusCode": 500}}, "HeadBucket")
        await self._writer(monkeypatch, err).setup()   # must not raise

    async def test_endpoint_unreachable_warns_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unreachable endpoint at startup buffers instead of crash-looping the workload."""
        err = EndpointConnectionError(endpoint_url="https://s3.example.invalid")
        await self._writer(monkeypatch, err).setup()   # must not raise

    async def test_unclassified_error_warns_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Anything unclassified defaults to buffer-and-retry, not a crash."""
        await self._writer(monkeypatch, RuntimeError("weird")).setup()   # must not raise


@pytest.mark.asyncio
class TestWriteBatch:
    """Streaming a batch to a temp file and uploading it."""

    async def test_uploads_under_key_and_cleans_temp(self, monkeypatch: pytest.MonkeyPatch, seeded: Store) -> None:
        """A non-empty batch uploads one object named by cursor, and the temp file is removed."""
        fake = _FakeBoto()
        monkeypatch.setattr(writer_mod, "boto3", fake)
        w = S3Writer(_cfg(prefix="raw/"), "json")
        r = await w.write_batch(seeded, 100)
        assert r.n_rows == 1 and len(fake.obj.uploads) == 1
        bucket, key, existed = fake.obj.uploads[0]
        assert bucket == "telemetry" and existed
        # key = prefix/batch-<utc-timestamp>-<cursor>.<fmt>, timestamp taken at upload time
        assert re.fullmatch(rf"raw/batch-\d{{8}}T\d{{6}}-{r.cursor}\.json", key)
        assert not os.path.exists(r.path)              # temp file cleaned up

    async def test_empty_buffer_uploads_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty buffer produces no upload and a None cursor."""
        fake = _FakeBoto()
        monkeypatch.setattr(writer_mod, "boto3", fake)
        store = Store(":memory:")
        await store.setup()
        r = await S3Writer(_cfg(), "parquet").write_batch(store, 100)
        assert r.n_rows == 0 and r.cursor is None and fake.obj.uploads == []

    async def test_upload_failure_resets_client_and_reraises(self, monkeypatch: pytest.MonkeyPatch, seeded: Store) -> None:
        """A failed upload resets the client (so the retry reconnects) and re-raises."""
        fake = _FakeBoto()
        fake.obj.fail = True
        monkeypatch.setattr(writer_mod, "boto3", fake)
        w = S3Writer(_cfg(), "parquet")
        with pytest.raises(RuntimeError, match="network down"):
            await w.write_batch(seeded, 100)
        assert w._client is None
