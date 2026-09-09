"""Real-server integration tests against a live MinIO S3 server (testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. These drive the real
`S3Writer` (boto3 over the wire) and the real `drain._tick` against a MinIO container, then read
the object back off the bucket. This is the path the unit tests fake (head_bucket/upload_file).

The connector builds its boto3 client without an `endpoint_url` (it targets real AWS), so each
test wraps `boto3.client` to point the otherwise-unchanged writer at MinIO with path-style
addressing.

One MinIO container is shared for the module. Each test gets a fresh, uniquely named bucket and a
fresh in-memory Store, and cleans the bucket up in a finally so tests never see each other's state.
"""
import csv
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
import duckdb
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

import writer as writer_mod
from settings import Settings
from store import Store
from writer import S3Writer

pytestmark = pytest.mark.integration


class _NoopClock:
    """Minimal ClockInterface stand-in for `_tick`: it only ever calls `sleep`."""

    async def sleep(self, delay: float) -> None:
        return None


@pytest.fixture(scope="module")
def minio():
    """Boot one MinIO server for the module; yields (config dict, the minio client)."""
    from testcontainers.minio import MinioContainer

    with MinioContainer() as c:
        yield c.get_config(), c.get_client()


def _point_boto3_at_minio(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    """Redirect the writer's boto3 client at MinIO without otherwise touching the writer."""
    endpoint = f"http://{config['endpoint']}"
    real_client = boto3.client
    monkeypatch.setattr(writer_mod.boto3, "client", lambda service, **kw: real_client(
        service, endpoint_url=endpoint, config=Config(s3={"addressing_style": "path"}), **kw))


def _s3_config(config: dict, bucket: str, prefix: str = "",
               access_key: str | None = None, secret_key: str | None = None):
    """Build the settings.S3 block, defaulting to MinIO's real root credentials."""
    return Settings(s3={
        "region": "us-east-1", "bucket": bucket, "prefix": prefix,
        "auth": {"access_key_id": access_key or config["access_key"],
                 "secret_access_key": secret_key or config["secret_key"]},
    }).s3


def _fresh_bucket(client) -> str:
    """Create a uniquely named bucket so no two tests share object namespace."""
    name = f"exports-{uuid.uuid4().hex[:12]}"
    client.make_bucket(name)
    return name


def _drop_bucket(client, bucket: str) -> None:
    for obj in client.list_objects(bucket, recursive=True):
        client.remove_object(bucket, obj.object_name)
    client.remove_bucket(bucket)


async def _store_with_rows() -> Store:
    """Fresh in-memory buffer holding one number row and one string row."""
    store = Store(":memory:")
    await store.setup()
    ts = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    await store.append(ts, "pump-1", "temperature", 42.5)
    await store.append(ts, "pump-1", "status", "running")
    return store


def _download(client, bucket: str, key: str, suffix: str) -> str:
    """Fetch an object off the bucket to a local temp file; returns its path."""
    fd, path = tempfile.mkstemp(prefix="dl-", suffix=suffix)
    os.close(fd)
    Path(path).unlink()          # fget_object writes the file itself
    client.fget_object(bucket, key, path)
    return path


def _parse_payloads(fmt: str, path: str) -> dict:
    """Read a downloaded export back into {datastream: native payload value}.

    The buffer stores payloads as a scalar-JSON *string* per row; parquet/csv carry that text
    verbatim (so json.loads recovers the native scalar), while the json export embeds it raw.
    """
    if fmt == "parquet":
        rows = duckdb.connect(":memory:").execute(
            f"SELECT datastream, payload FROM read_parquet('{path}')").fetchall()
        return {ds: json.loads(p) for ds, p in rows}
    if fmt == "csv":
        with open(path, newline="") as f:
            return {row["datastream"]: json.loads(row["payload"]) for row in csv.DictReader(f)}
    if fmt == "json":                       # DuckDB writes newline-delimited JSON objects
        out = {}
        for line in Path(path).read_text().splitlines():
            if line.strip():
                obj = json.loads(line)
                out[obj["datastream"]] = obj["payload"]
        return out
    raise AssertionError(f"unhandled format {fmt!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt, prefix", [("parquet", "raw/"), ("csv", ""), ("json", "")])
async def test_format_round_trip(minio, monkeypatch: pytest.MonkeyPatch, fmt: str, prefix: str) -> None:
    """Upload one batch in each format, download the object, parse it, and assert the native
    payload values survive the round trip. parquet also carries a prefix, so this doubles as the
    "prefix places the object under a folder" check."""
    config, client = minio
    _point_boto3_at_minio(monkeypatch, config)
    bucket = _fresh_bucket(client)
    cfg = _s3_config(config, bucket, prefix=prefix)
    store = await _store_with_rows()
    s3writer = S3Writer(cfg, fmt)
    local = None
    try:
        await s3writer.setup()
        result = await s3writer.write_batch(store, limit=1000)
        assert result.cursor is not None and result.n_rows == 2

        # Key is <prefix>batch-<utc-timestamp>-<cursor>.<fmt>; timestamp is taken at upload
        # time, so locate the object by listing the (single-object) bucket.
        objs = [o.object_name for o in client.list_objects(bucket, prefix=prefix, recursive=True)]
        assert len(objs) == 1, f"expected one object, got {objs}"
        key = objs[0]
        assert re.fullmatch(rf"{re.escape(prefix)}batch-\d{{8}}T\d{{6}}-{result.cursor}\.{fmt}", key)
        if prefix:
            assert key.startswith(prefix)

        local = _download(client, bucket, key, suffix=f".{fmt}")
        payloads = _parse_payloads(fmt, local)
        assert payloads == {"temperature": 42.5, "status": "running"}
    finally:
        await s3writer.teardown()
        await store.teardown()
        if local:
            Path(local).unlink(missing_ok=True)
        _drop_bucket(client, bucket)


@pytest.mark.asyncio
async def test_tick_uploads_then_drops_buffer(minio, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real drain cycle delivers the batch AND empties the buffer: at-least-once means the
    rows are dropped only after the upload is confirmed on the wire."""
    import drain

    config, client = minio
    _point_boto3_at_minio(monkeypatch, config)
    bucket = _fresh_bucket(client)
    cfg = _s3_config(config, bucket)
    settings = Settings(s3={"region": "us-east-1", "bucket": bucket,
                            "auth": {"access_key_id": config["access_key"],
                                     "secret_access_key": config["secret_key"]}})
    store = await _store_with_rows()
    s3writer = S3Writer(cfg, "parquet")
    try:
        await s3writer.setup()
        assert await store.count() == 2
        r = await drain._tick(s3writer, store, settings.upload, settings.buffer, _NoopClock())

        assert r is not None and r.n_rows == 2
        assert await store.count() == 0, "buffer must be empty after a confirmed upload"
        objs = list(client.list_objects(bucket, recursive=True))
        assert len(objs) == 1 and objs[0].size > 0
    finally:
        await s3writer.teardown()
        await store.teardown()
        _drop_bucket(client, bucket)


@pytest.mark.asyncio
async def test_setup_fails_fast_on_missing_bucket(minio, monkeypatch: pytest.MonkeyPatch) -> None:
    """head_bucket against a bucket that doesn't exist raises a real 404/NoSuchBucket, which
    `_CONFIG_ERROR_CODES`/`_CONFIG_ERROR_STATUSES` classify as fatal misconfiguration."""
    config, client = minio
    _point_boto3_at_minio(monkeypatch, config)
    cfg = _s3_config(config, bucket=f"missing-{uuid.uuid4().hex[:12]}")
    s3writer = S3Writer(cfg, "parquet")
    try:
        with pytest.raises(ClientError) as excinfo:
            await s3writer.setup()
        err = excinfo.value.response
        code = err.get("Error", {}).get("Code", "")
        status = err.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        assert (code in writer_mod._CONFIG_ERROR_CODES
                or status in writer_mod._CONFIG_ERROR_STATUSES), (code, status)
    finally:
        await s3writer.teardown()


@pytest.mark.asyncio
async def test_setup_fails_fast_on_bad_credentials(minio, monkeypatch: pytest.MonkeyPatch) -> None:
    """head_bucket with wrong credentials raises a real 403 (InvalidAccessKeyId /
    SignatureDoesNotMatch), proving the config-error classification matches the wire codes."""
    config, client = minio
    _point_boto3_at_minio(monkeypatch, config)
    bucket = _fresh_bucket(client)
    cfg = _s3_config(config, bucket, access_key="wrong-key",
                     secret_key="wrong-secret-that-will-not-verify")
    s3writer = S3Writer(cfg, "parquet")
    try:
        with pytest.raises(ClientError) as excinfo:
            await s3writer.setup()
        err = excinfo.value.response
        code = err.get("Error", {}).get("Code", "")
        status = err.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        assert (code in writer_mod._CONFIG_ERROR_CODES
                or status in writer_mod._CONFIG_ERROR_STATUSES), (code, status)
    finally:
        await s3writer.teardown()
        _drop_bucket(client, bucket)


@pytest.mark.asyncio
async def test_batch_uploads_to_bucket(minio, monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke: a written batch lands as a nonempty object under the batch-<ts>-<cursor> key."""
    config, client = minio
    _point_boto3_at_minio(monkeypatch, config)
    bucket = _fresh_bucket(client)
    cfg = _s3_config(config, bucket)
    store = await _store_with_rows()
    s3writer = S3Writer(cfg, "csv")
    try:
        await s3writer.setup()
        result = await s3writer.write_batch(store, limit=1000)
        assert result.cursor is not None and result.n_rows == 2
        pattern = re.compile(rf"batch-\d{{8}}T\d{{6}}-{result.cursor}\.csv")
        keys = [o.object_name for o in client.list_objects(bucket, recursive=True)]
        matches = [k for k in keys if pattern.fullmatch(k)]
        assert matches, f"no object matching {pattern.pattern} in {keys}"
        assert client.stat_object(bucket, matches[0]).size > 0
    finally:
        await s3writer.teardown()
        await store.teardown()
        _drop_bucket(client, bucket)
