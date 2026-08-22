"""Unit tests for VolumeWriter: client building, volume path, temp-file lifecycle (no cloud)."""
import os
import re
from datetime import datetime

import pytest
import pytest_asyncio
from databricks.sdk.errors import Unauthenticated

import writer as writer_mod
from settings import Settings
from store import Store
from writer import VolumeWriter

DB = {"server_hostname": "dbc-123.cloud.databricks.com",
      "delta_table": "main.telemetry.readings",
      "uc_volume": "main.telemetry.landing"}
OAUTH = {"method": "oauth", "client_id": "cid", "client_secret": "csec"}
TOKEN = {"method": "access_token", "access_token": "tok"}


def _cfg(auth=OAUTH, **over):
    return Settings(databricks={**DB, "auth": auth, **over}).databricks


class _FakeFiles:
    def __init__(self) -> None:
        self.uploads: list[tuple] = []
        self.fail = False

    def upload(self, volume_path, contents, overwrite=False):
        if self.fail:
            raise RuntimeError("network down")
        # contents is an open file handle; record path + whether the source still exists.
        self.uploads.append((volume_path, overwrite, os.path.exists(contents.name)))


class _FakeWorkspaceClient:
    instances: list["_FakeWorkspaceClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.files = _FakeFiles()
        self.me_called = False
        self.me_error: Exception | None = None
        _FakeWorkspaceClient.instances.append(self)

    @property
    def current_user(self):
        client = self

        class _CU:
            def me(_self):
                client.me_called = True
                if client.me_error is not None:
                    raise client.me_error
        return _CU()


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type:
    """Replace the WorkspaceClient symbol and stub the job helpers so setup() makes no real calls."""
    _FakeWorkspaceClient.instances = []
    monkeypatch.setattr(writer_mod, "WorkspaceClient", _FakeWorkspaceClient)
    monkeypatch.setattr(writer_mod.job, "create_job_copy_into", lambda *a, **k: None)
    monkeypatch.setattr(writer_mod.job, "create_job_autoloader", lambda *a, **k: None)
    return _FakeWorkspaceClient


@pytest_asyncio.fixture
async def seeded() -> Store:
    s = Store(":memory:")
    await s.setup()
    await s.append(datetime(2026, 1, 1), "pump-1", "temperature", 42.5)
    return s


class TestConnection:
    """WorkspaceClient construction (synchronous)."""

    def test_oauth_unwraps_secret_credentials(self) -> None:
        """OAuth credentials are passed to the SDK unwrapped from SecretStr, host scheme prepended."""
        w = VolumeWriter(_cfg(auth=OAUTH), "parquet")
        client = w._connection()
        assert client.kwargs["client_id"] == "cid"
        assert client.kwargs["client_secret"] == "csec"
        assert client.kwargs["host"] == "https://dbc-123.cloud.databricks.com"

    def test_access_token_unwraps_secret(self) -> None:
        """Token auth passes the unwrapped token, not a SecretStr."""
        w = VolumeWriter(_cfg(auth=TOKEN), "parquet")
        client = w._connection()
        assert client.kwargs["token"] == "tok"
        assert "client_id" not in client.kwargs

    def test_client_is_reused(self) -> None:
        """_connection() builds the client once and reuses it."""
        w = VolumeWriter(_cfg(), "parquet")
        assert w._connection() is w._connection()


class TestVolumePath:
    """uc_volume -> /Volumes/<cat>/<schema>/<vol>/data/batch-<utc>-<cursor>.<fmt>."""

    def test_constructs_data_path_from_volume(self) -> None:
        """Path embeds the volume triplet, an upload-time UTC stamp, and the batch cursor."""
        w = VolumeWriter(_cfg(), "csv")
        assert re.fullmatch(
            r"/Volumes/main/telemetry/landing/data/batch-\d{8}T\d{6}-7\.csv",
            w._volume_data_path(7),
        )


@pytest.mark.asyncio
class TestSetup:
    """setup() validates connectivity and ensures the ingestion job."""

    async def test_copy_into_job_when_warehouse_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A warehouse_id triggers create_job_copy_into with FILEFORMAT-driving fmt."""
        calls: list[dict] = []
        monkeypatch.setattr(writer_mod.job, "create_job_copy_into", lambda *a, **k: calls.append(k))
        w = VolumeWriter(_cfg(job={"warehouse_id": "wh-1"}), "csv")
        await w.setup()
        assert calls and calls[0]["warehouse_id"] == "wh-1" and calls[0]["fmt"] == "csv"
        assert w._connection().me_called

    async def test_autoloader_job_when_cluster_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A cluster_id (no warehouse) triggers create_job_autoloader."""
        calls: list[dict] = []
        monkeypatch.setattr(writer_mod.job, "create_job_autoloader", lambda *a, **k: calls.append(k))
        w = VolumeWriter(_cfg(job={"cluster_id": "cl-1"}), "parquet")
        await w.setup()
        assert calls and calls[0]["cluster_id"] == "cl-1"

    async def test_no_job_when_neither_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No warehouse/cluster id creates no ingestion job, just validates connectivity."""
        copy_calls, auto_calls = [], []
        monkeypatch.setattr(writer_mod.job, "create_job_copy_into", lambda *a, **k: copy_calls.append(k))
        monkeypatch.setattr(writer_mod.job, "create_job_autoloader", lambda *a, **k: auto_calls.append(k))
        w = VolumeWriter(_cfg(), "parquet")
        await w.setup()
        assert not copy_calls and not auto_calls and w._connection().me_called

    async def test_ready_logged_when_probe_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful connectivity probe logs the 'Volume writer ready' line."""
        infos: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "info", lambda msg, **k: infos.append(msg))
        w = VolumeWriter(_cfg(), "parquet")
        await w.setup()
        assert "Volume writer ready" in infos

    async def test_transient_connectivity_failure_warns_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A transient failure (network blip, 5xx) doesn't kill setup; uploads retry later.
        The 'ready' line is suppressed: the writer starts, but the probe didn't succeed."""
        infos: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "info", lambda msg, **k: infos.append(msg))
        w = VolumeWriter(_cfg(), "parquet")
        w._connection().me_error = ConnectionError("dns hiccup")
        await w.setup()                            # no raise: the writer starts anyway
        assert w._connection().me_called
        assert "Volume writer ready" not in infos

    async def test_deterministic_config_error_fails_setup(self) -> None:
        """A config error (bad credentials/host/ids) raises: retrying can't fix it."""
        w = VolumeWriter(_cfg(), "parquet")
        w._connection().me_error = Unauthenticated("bad token")
        with pytest.raises(Unauthenticated):
            await w.setup()


@pytest.mark.asyncio
class TestWriteBatch:
    """Streaming a batch to a temp file and uploading it to the volume."""

    async def test_uploads_to_volume_path_and_cleans_temp(self, seeded: Store) -> None:
        """A non-empty batch uploads one file named by stamp + cursor, streamed from the temp file, then removed."""
        w = VolumeWriter(_cfg(), "csv")
        r = await w.write_batch(seeded, 100)
        client = w._connection()
        assert r.n_rows == 1 and len(client.files.uploads) == 1
        volume_path, overwrite, existed_at_upload = client.files.uploads[0]
        assert re.fullmatch(
            rf"/Volumes/main/telemetry/landing/data/batch-\d{{8}}T\d{{6}}-{r.cursor}\.csv",
            volume_path,
        )
        assert overwrite is True and existed_at_upload
        assert not os.path.exists(r.path)              # temp file cleaned up

    async def test_empty_buffer_uploads_nothing(self) -> None:
        """An empty buffer produces no upload and a None cursor."""
        store = Store(":memory:")
        await store.setup()
        w = VolumeWriter(_cfg(), "parquet")
        r = await w.write_batch(store, 100)
        assert r.n_rows == 0 and r.cursor is None
        # Build a connection to inspect: no uploads recorded.
        assert w._client is None or w._client.files.uploads == []

    async def test_upload_failure_resets_client_and_reraises(self, seeded: Store) -> None:
        """A failed upload resets the client (so the retry reconnects) and re-raises."""
        w = VolumeWriter(_cfg(), "parquet")
        w._connection().files.fail = True
        with pytest.raises(RuntimeError, match="network down"):
            await w.write_batch(seeded, 100)
        assert w._client is None
