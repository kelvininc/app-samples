"""Unit tests for SftpWriter: connection/auth, host-key policy, upload + temp-file lifecycle (no network)."""
import os
import posixpath
import re
from datetime import datetime

import paramiko
import pytest
import pytest_asyncio

import writer as writer_mod
from settings import Settings
from store import Store
from writer import SftpWriter

SFTP = {"host": "sftp.example.com", "username": "svc"}


def _cfg(auth=None, **over):
    auth = auth or {"method": "password", "password": "pw"}
    return Settings(sftp={**SFTP, **over, "auth": auth}).sftp


class _FakeChannel:
    def __init__(self) -> None:
        self.timeouts: list = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)


class _FakeSftp:
    def __init__(self, owner: "_FakeClient") -> None:
        self.owner = owner
        self.channel = _FakeChannel()
        self.stat_exc: Exception | None = None
        self.posix_rename_exc: Exception | None = None   # None: server supports posix_rename
        self.rename_exc: Exception | None = None         # None: plain rename fallback succeeds

    def get_channel(self):
        return self.channel

    def stat(self, path):
        if self.stat_exc is not None:
            raise self.stat_exc
        self.owner.statted.append(path)

    def put(self, local, remote):
        if self.owner.fail:
            raise RuntimeError("network down")
        self.owner.puts.append((remote, os.path.exists(local)))   # source present at upload time?

    def posix_rename(self, src, dst):
        if self.posix_rename_exc is not None:
            raise self.posix_rename_exc
        self.owner.renames.append(("posix", src, dst))

    def rename(self, src, dst):
        if self.rename_exc is not None:
            raise self.rename_exc
        self.owner.renames.append(("plain", src, dst))

    def remove(self, path):
        self.owner.removed.append(path)

    def close(self):
        self.owner.sftp_closed = True


class _FakeClient:
    def __init__(self) -> None:
        self.policy = None
        self.loaded_system = False
        self.loaded_hosts = None
        self.host_keys = {"sftp.example.com": "ssh-ed25519 AAAA"}   # non-empty: keys "loaded"
        self.load_hosts_exc: Exception | None = None
        self.connect_exc: Exception | None = None
        self.open_sftp_exc: Exception | None = None
        self.connect_kwargs = None
        self.puts: list = []
        self.statted: list = []
        self.renames: list = []                          # (kind, src, dst) publish steps
        self.removed: list = []                          # temp files cleaned up
        self.fail = False
        self.sftp_closed = False
        self.closed = False
        self._sftp = _FakeSftp(self)

    def load_system_host_keys(self):
        self.loaded_system = True

    def load_host_keys(self, path):
        if self.load_hosts_exc is not None:
            raise self.load_hosts_exc
        self.loaded_hosts = path

    def get_host_keys(self):
        return self.host_keys

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        if self.connect_exc is not None:
            raise self.connect_exc
        self.connect_kwargs = kwargs

    def open_sftp(self):
        if self.open_sftp_exc is not None:
            raise self.open_sftp_exc
        return self._sftp

    def close(self):
        self.closed = True


def _patch_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(writer_mod.paramiko, "SSHClient", lambda: fake)
    return fake


@pytest_asyncio.fixture
async def seeded() -> Store:
    s = Store(":memory:")
    await s.setup()
    await s.append(datetime(2026, 1, 1), "pump-1", "temperature", 42.5)
    return s


class TestConnection:
    def test_password_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        SftpWriter(_cfg(), "parquet")._connection()
        assert fake.connect_kwargs["password"] == "pw"
        assert fake.connect_kwargs["hostname"] == "sftp.example.com" and fake.connect_kwargs["username"] == "svc"

    def test_private_key_auth_loads_pkey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        sentinel = object()
        monkeypatch.setattr(writer_mod, "_load_pkey", lambda pem, passphrase: sentinel)
        SftpWriter(_cfg(auth={"method": "private_key", "private_key": "-----K-----"}), "parquet")._connection()
        assert fake.connect_kwargs["pkey"] is sentinel and "password" not in fake.connect_kwargs

    def test_verify_host_key_uses_reject_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        SftpWriter(_cfg(verify_host_key=True), "parquet")._connection()
        assert fake.loaded_system is True and isinstance(fake.policy, paramiko.RejectPolicy)

    def test_known_hosts_path_is_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        SftpWriter(_cfg(known_hosts="/etc/known_hosts"), "parquet")._connection()
        assert fake.loaded_hosts == "/etc/known_hosts"

    def test_unverified_uses_auto_add_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        SftpWriter(_cfg(verify_host_key=False), "parquet")._connection()
        assert isinstance(fake.policy, paramiko.AutoAddPolicy)

    def test_verify_with_zero_loaded_keys_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RejectPolicy with an empty key set can never connect: fail with guidance, before connecting."""
        fake = _patch_client(monkeypatch)
        fake.host_keys = {}
        with pytest.raises(RuntimeError, match="no host keys were loaded"):
            SftpWriter(_cfg(verify_host_key=True), "parquet")._connection()
        assert fake.connect_kwargs is None             # never attempted the connection

    def test_connect_and_channel_timeouts_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        SftpWriter(_cfg(timeout=7), "parquet")._connection()
        kw = fake.connect_kwargs
        assert kw["timeout"] == kw["banner_timeout"] == kw["auth_timeout"] == 7
        assert fake._sftp.channel.timeouts == [7]      # transfer socket bounded too

    def test_default_timeout_is_30s(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        SftpWriter(_cfg(), "parquet")._connection()
        assert fake.connect_kwargs["timeout"] == 30

    def test_open_sftp_failure_closes_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed open_sftp() must not leak the connected transport or keep a half-built client."""
        fake = _patch_client(monkeypatch)
        fake.open_sftp_exc = paramiko.SSHException("channel failed")
        w = SftpWriter(_cfg(), "parquet")
        with pytest.raises(paramiko.SSHException, match="channel failed"):
            w._connection()
        assert fake.closed and w._client is None and w._sftp is None


@pytest.mark.asyncio
class TestSetupClassification:
    """setup() policy: deterministic config errors crash; transient/unknown warn and buffer-and-retry."""

    async def test_auth_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        fake.connect_exc = paramiko.AuthenticationException("bad credentials")
        with pytest.raises(paramiko.AuthenticationException):
            await SftpWriter(_cfg(), "parquet").setup()
        assert fake.closed                             # leak fix closed the client on the way out

    async def test_zero_host_keys_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        fake.host_keys = {}
        with pytest.raises(RuntimeError, match="no host keys were loaded"):
            await SftpWriter(_cfg(), "parquet").setup()

    async def test_missing_known_hosts_file_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A configured-but-missing known_hosts path is a deployment error, not a transient one."""
        fake = _patch_client(monkeypatch)
        fake.load_hosts_exc = FileNotFoundError("/missing/known_hosts")   # what paramiko raises for a bad path
        with pytest.raises(RuntimeError, match="could not read sftp.known_hosts"):
            await SftpWriter(_cfg(known_hosts="/missing/known_hosts"), "parquet").setup()
        assert fake.connect_kwargs is None             # never attempted the connection

    async def test_missing_remote_dir_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        fake._sftp.stat_exc = FileNotFoundError("/missing")
        with pytest.raises(FileNotFoundError):
            await SftpWriter(_cfg(remote_dir="/missing"), "parquet").setup()

    @pytest.mark.parametrize("exc", [ConnectionRefusedError("refused"), TimeoutError("timed out"),
                                     OSError("network unreachable")])
    async def test_transient_connect_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch,
                                                          exc: Exception) -> None:
        fake = _patch_client(monkeypatch)
        fake.connect_exc = exc
        await SftpWriter(_cfg(), "parquet").setup()    # logs a warning, does not raise

    async def test_unknown_host_key_logs_hostkey_wording(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RejectPolicy's plain SSHException gets the host-key wording, not the generic
        'unreachable' one, and setup still swallows it (known_hosts may still be deploying)."""
        fake = _patch_client(monkeypatch)
        fake.connect_exc = paramiko.SSHException("Server 'h' not found in known_hosts")
        warnings: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "warning",
                            lambda msg, **kw: warnings.append(msg))
        await SftpWriter(_cfg(), "parquet").setup()    # logged, not raised
        assert warnings == ["Host key verification failed: server key not in known_hosts; "
                            "buffering and retrying"]

    async def test_generic_ssh_failure_logs_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An SSHException without the known_hosts marker keeps the generic wording."""
        fake = _patch_client(monkeypatch)
        fake.connect_exc = paramiko.SSHException("Error reading SSH protocol banner")
        warnings: list[str] = []
        monkeypatch.setattr(writer_mod.logger, "warning",
                            lambda msg, **kw: warnings.append(msg))
        await SftpWriter(_cfg(), "parquet").setup()    # logged, not raised
        assert warnings == ["SFTP unreachable at setup; buffering and retrying"]

    async def test_transient_stat_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        fake._sftp.stat_exc = paramiko.SSHException("connection dropped")
        await SftpWriter(_cfg(), "parquet").setup()    # logs a warning, does not raise

    async def test_stat_connection_reset_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A dropped connection during the stat check is transient, not a config error."""
        fake = _patch_client(monkeypatch)
        fake._sftp.stat_exc = ConnectionResetError("connection reset by peer")
        await SftpWriter(_cfg(), "parquet").setup()    # logs a warning, does not raise


@pytest.mark.asyncio
class TestWriteBatch:
    @pytest.mark.parametrize("posix_exc, kind, fmt", [
        (None, "posix", "json"),                       # server supports posix_rename@openssh.com
        (IOError("operation unsupported"), "plain", "parquet"),   # falls back to plain rename()
    ])
    async def test_uploads_temp_then_atomic_rename(self, monkeypatch: pytest.MonkeyPatch, seeded: Store,
                                                   posix_exc: Exception | None, kind: str, fmt: str) -> None:
        """The batch is put to a hidden .part sibling, then renamed into the final name so a
        consumer never reads a half-written file. posix_rename is used when the server supports
        it; a server that lacks the extension makes it raise and the writer falls back to rename()."""
        fake = _patch_client(monkeypatch)
        fake._sftp.posix_rename_exc = posix_exc
        w = SftpWriter(_cfg(remote_dir="/incoming"), fmt)
        r = await w.write_batch(seeded, 100)
        assert r.n_rows == 1 and len(fake.renames) == 1

        # The final name is reached only via the rename; the .part is its hidden sibling.
        recorded_kind, src, dst = fake.renames[0]
        assert recorded_kind == kind
        assert re.fullmatch(rf"/incoming/batch-\d{{8}}T\d{{6}}-{r.cursor}\.{fmt}", dst)
        final_dir, final_name = posixpath.split(dst)
        expected_part = posixpath.join(final_dir, f".{final_name}.part")
        assert src == expected_part

        # put() targets that .part temp, streaming the local temp file (present at upload time).
        tmp_remote, existed = fake.puts[0]
        assert tmp_remote == expected_part and existed
        assert fake.removed == []                      # nothing to clean up on success
        assert not os.path.exists(r.path)              # local temp file cleaned up

    async def test_empty_buffer_uploads_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch)
        store = Store(":memory:")
        await store.setup()
        r = await SftpWriter(_cfg(), "parquet").write_batch(store, 100)
        assert r.n_rows == 0 and r.cursor is None and fake.puts == [] and fake.renames == []

    @pytest.mark.parametrize("fault, exc_type, match, put_landed", [
        # put() fails before any rename: nothing was uploaded.
        pytest.param({"fail": True},
                     RuntimeError, "network down", False,
                     id="put-fails-before-rename"),
        # put() lands a .part; posix_rename raises (fallback path) and the plain rename also fails.
        pytest.param({"posix_rename_exc": IOError("operation unsupported"),
                      "rename_exc": paramiko.SSHException("rename failed")},
                     paramiko.SSHException, "rename failed", True,
                     id="both-renames-fail"),
        # posix_rename raises outside the fallback tuple (RuntimeError): plain rename must NOT run.
        pytest.param({"posix_rename_exc": RuntimeError("unexpected")},
                     RuntimeError, "unexpected", True,
                     id="non-fallback-posix-exc-skips-plain-rename"),
    ])
    async def test_upload_failure_cleans_part_resets_and_reraises(
        self, monkeypatch: pytest.MonkeyPatch, seeded: Store,
        fault: dict, exc_type: type[Exception], match: str, put_landed: bool,
    ) -> None:
        """Any upload failure (put down, both renames fail, or a non-fallback posix_rename error)
        obeys one contract: nothing is published, the orphaned .part is cleaned up, the connection
        is dropped, and the error re-raises so the drain retries the whole batch (buffer not trimmed).
        A non-fallback posix_rename error also proves the plain rename never ran (renames stays empty)."""
        fake = _patch_client(monkeypatch)
        fake.fail = fault.get("fail", False)
        fake._sftp.posix_rename_exc = fault.get("posix_rename_exc")
        fake._sftp.rename_exc = fault.get("rename_exc")
        w = SftpWriter(_cfg(), "parquet")
        with pytest.raises(exc_type, match=match):
            await w.write_batch(seeded, 100)
        # Shared failure contract: no publish, exactly one .part cleaned up, connection reset.
        assert fake.renames == []                      # no rename recorded a successful publish
        assert len(fake.removed) == 1 and fake.removed[0].endswith(".part")
        assert w._sftp is None and fake.sftp_closed and fake.closed
        # Did put() land a .part before the failure?
        if put_landed:
            assert len(fake.puts) == 1 and fake.puts[0][0].endswith(".part")
            assert fake.removed[0] == fake.puts[0][0]   # the exact .part put is the one removed
        else:
            assert fake.puts == []
