"""Unit tests for ADLSWriter: client building, object naming, and temp-file lifecycle (no cloud)."""
import os
import re
from datetime import datetime

import pytest
import pytest_asyncio
from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError, ServiceRequestError

import writer as writer_mod
from settings import Settings
from store import Store
from writer import ADLSWriter


def _cfg(**over):
    return Settings(adls={"account_name": "telemetrylake", "container": "raw", **over}).adls


@pytest.fixture(autouse=True)
def _no_real_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never construct a real DefaultAzureCredential in tests (it would reach out for a token)."""
    class _FakeCred:
        async def close(self) -> None: ...
    monkeypatch.setattr(writer_mod, "DefaultAzureCredential", lambda *a, **k: _FakeCred())


class _FakeFileClient:
    def __init__(self, owner: "_FakeServiceClient") -> None:
        self.owner = owner

    async def upload_data(self, data, length, overwrite):
        if self.owner.fail:
            raise RuntimeError("network down")
        # Record the raw upload payload (name, declared length, actual data) so the test
        # body can assert on the bytes read off the event loop, never a sync file handle.
        self.owner.uploads.append((self.owner.name, length, data))


class _FakeFileSystemClient:
    def __init__(self, owner: "_FakeServiceClient") -> None:
        self.owner = owner

    async def get_file_system_properties(self):
        if self.owner.setup_exc is not None:
            raise self.owner.setup_exc
        return {}

    def get_file_client(self, name):
        self.owner.name = name
        return _FakeFileClient(self.owner)


class _FakeServiceClient:
    def __init__(self) -> None:
        self.uploads: list[tuple] = []
        self.fail = False
        self.setup_exc: Exception | None = None
        self.closed = False
        self.name = None

    def get_file_system_client(self, container):
        self.container = container
        return _FakeFileSystemClient(self)

    async def close(self):
        self.closed = True


class _FakeServiceClientFactory:
    """Stands in for the DataLakeServiceClient class; records constructor kwargs."""

    def __init__(self) -> None:
        self.kwargs = None
        self.obj = _FakeServiceClient()

    def __call__(self, **kw):
        self.kwargs = kw
        return self.obj


@pytest_asyncio.fixture
async def seeded() -> Store:
    s = Store(":memory:")
    await s.setup()
    await s.append(datetime(2026, 1, 1), "pump-1", "temperature", 42.5)
    return s


class TestConnection:
    """DataLakeServiceClient construction."""

    def test_unwraps_secret_account_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The account key is passed to the azure client unwrapped from SecretStr."""
        fake = _FakeServiceClientFactory()
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        ADLSWriter(_cfg(auth={"account_key": "shhh"}), "parquet")._connection()
        assert fake.kwargs["credential"] == "shhh"
        assert fake.kwargs["account_url"] == "https://telemetrylake.dfs.core.windows.net"

    def test_no_key_uses_default_azure_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no account key, the client is built with DefaultAzureCredential (managed identity)."""
        fake = _FakeServiceClientFactory()
        sentinel = object()
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        monkeypatch.setattr(writer_mod, "DefaultAzureCredential", lambda *a, **k: sentinel)
        w = ADLSWriter(_cfg(), "parquet")
        w._connection()
        assert fake.kwargs["credential"] is sentinel
        assert w._credential is sentinel        # tracked so _reset/teardown can close it


@pytest.mark.asyncio
class TestSetup:
    """setup() fail-fasts on deterministic config errors, warns and continues otherwise."""

    async def test_setup_checks_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A reachable container passes setup()."""
        fake = _FakeServiceClientFactory()
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        await ADLSWriter(_cfg(auth={"account_key": "shhh"}), "parquet").setup()
        assert fake.obj.container == "raw"

    @pytest.mark.parametrize("exc", [ResourceNotFoundError("container missing"),
                                     ClientAuthenticationError("bad credentials")])
    async def test_setup_raises_on_config_error(self, monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
        """A missing container or an auth failure is a deterministic config error: setup() raises."""
        fake = _FakeServiceClientFactory()
        fake.obj.setup_exc = exc
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        with pytest.raises(type(exc)):
            await ADLSWriter(_cfg(), "parquet").setup()

    @pytest.mark.parametrize("exc", [ServiceRequestError("dns lookup failed"),
                                     RuntimeError("weird transport state")])
    async def test_setup_continues_on_transient_error(self, monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
        """A transient or unknown connectivity failure only warns: the drain retries uploads anyway."""
        fake = _FakeServiceClientFactory()
        fake.obj.setup_exc = exc
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        await ADLSWriter(_cfg(), "parquet").setup()     # must not raise
        assert fake.obj.container == "raw"


@pytest.mark.asyncio
class TestWriteBatch:
    """Streaming a batch to a temp file and uploading it."""

    async def test_uploads_under_name_and_cleans_temp(self, monkeypatch: pytest.MonkeyPatch, seeded: Store) -> None:
        """A non-empty batch uploads one object named batch-<utc-ts>-<cursor>, bytes read off the loop, temp removed."""
        fake = _FakeServiceClientFactory()
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        w = ADLSWriter(_cfg(), "json")
        r = await w.write_batch(seeded, 100)
        assert r.n_rows == 1 and len(fake.obj.uploads) == 1
        name, length, data = fake.obj.uploads[0]
        assert re.fullmatch(rf"batch-\d{{8}}T\d{{6}}-{r.cursor}\.json", name)
        assert isinstance(data, bytes)                 # uploaded bytes read off the loop, not a file handle
        assert len(data) == length > 0                 # declared length matches the bytes we got
        assert not os.path.exists(r.path)              # temp file cleaned up

    async def test_empty_buffer_uploads_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty buffer produces no upload and a None cursor."""
        fake = _FakeServiceClientFactory()
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        store = Store(":memory:")
        await store.setup()
        r = await ADLSWriter(_cfg(), "parquet").write_batch(store, 100)
        assert r.n_rows == 0 and r.cursor is None and fake.obj.uploads == []

    async def test_upload_failure_resets_client_and_reraises(self, monkeypatch: pytest.MonkeyPatch, seeded: Store) -> None:
        """A failed upload resets the client (so the retry reconnects) and re-raises."""
        fake = _FakeServiceClientFactory()
        fake.obj.fail = True
        monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
        w = ADLSWriter(_cfg(), "parquet")
        with pytest.raises(RuntimeError, match="network down"):
            await w.write_batch(seeded, 100)
        assert w._service_client is None
        assert fake.obj.closed, "stale client was not closed on reset"


@pytest.mark.asyncio
async def test_teardown_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """teardown() closes the underlying azure async client."""
    fake = _FakeServiceClientFactory()
    monkeypatch.setattr(writer_mod, "DataLakeServiceClient", fake)
    w = ADLSWriter(_cfg(), "parquet")
    w._connection()
    await w.teardown()
    assert fake.obj.closed and w._service_client is None
