"""Real-server smoke test (a live MinIO S3 server via testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. Drives the real `S3Writer`
(boto3 over the wire) against a MinIO container, then verifies the object landed; the actual
head_bucket/upload_file path the unit tests fake.

The connector builds its boto3 client without an `endpoint_url` (it targets real AWS), so the
test wraps `boto3.client` to point the otherwise-unchanged writer at MinIO with path-style
addressing.
"""
import re
from datetime import datetime, timezone

import boto3
import pytest
from botocore.config import Config

import writer as writer_mod
from settings import Settings
from store import Store
from writer import S3Writer

pytestmark = pytest.mark.integration

_BUCKET = "exports"


@pytest.fixture(scope="module")
def minio():
    """Boot one MinIO server for the module; yields (config dict, the minio client)."""
    from testcontainers.minio import MinioContainer

    with MinioContainer() as c:
        client = c.get_client()
        client.make_bucket(_BUCKET)
        yield c.get_config(), client


async def _store_with_rows() -> Store:
    store = Store(":memory:")
    await store.setup()
    ts = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    await store.append(ts, "pump-1", "temperature", 42.5)
    await store.append(ts, "pump-1", "status", "running")
    return store


@pytest.mark.asyncio
async def test_batch_uploads_to_bucket(minio, monkeypatch: pytest.MonkeyPatch) -> None:
    config, client = minio
    endpoint = f"http://{config['endpoint']}"

    real_client = boto3.client
    monkeypatch.setattr(writer_mod.boto3, "client", lambda service, **kw: real_client(
        service, endpoint_url=endpoint, config=Config(s3={"addressing_style": "path"}), **kw))

    cfg = Settings(s3={"region": "us-east-1", "bucket": _BUCKET,
                       "auth": {"access_key_id": config["access_key"],
                                "secret_access_key": config["secret_key"]}}).s3
    store = await _store_with_rows()
    s3writer = S3Writer(cfg, "csv")
    try:
        await s3writer.setup()
        result = await s3writer.write_batch(store, limit=1000)
    finally:
        await s3writer.teardown()
        await store.teardown()

    assert result.cursor is not None and result.n_rows == 2
    # Key is batch-<utc-timestamp>-<cursor>.<fmt>; the timestamp is taken at upload time,
    # so find the object by pattern instead of an exact name.
    pattern = re.compile(rf"batch-\d{{8}}T\d{{6}}-{result.cursor}\.csv")
    keys = [o.object_name for o in client.list_objects(_BUCKET)]
    matches = [k for k in keys if pattern.fullmatch(k)]
    assert matches, f"no object matching {pattern.pattern} in {keys}"
    assert client.stat_object(_BUCKET, matches[0]).size > 0
