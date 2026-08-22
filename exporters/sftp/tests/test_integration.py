"""Real-server smoke tests (a live SFTP server via testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. These drive the real
`SftpWriter` (paramiko over the wire) against an `atmoz/sftp` container, covering the things
the unit tests can only fake:

* an actual file upload that lands, complete and atomically (temp `.part` then rename);
* host-key verification, including the security default that rejects an unknown host (anti-MITM);
* recovery after a dropped transport (drop -> _reset -> rebuild);
* a real private-key auth handshake (paramiko key exchange over the wire);
* a real drain cycle (`drain._tick`) that uploads then drops the buffer.

One module-scoped container serves every test. It carries two users: `basic` (password) and
`keypair` (auto-generated RSA key). Each test writes into its own remote subdirectory (or the
keypair user's separate home) so listings never see another test's files.
"""
import asyncio
import io
import posixpath
import re
from datetime import datetime, timezone

import duckdb
import paramiko
import pytest

from drain import _tick
from kelvin.application.clock import ClockInterface
from settings import Buffer, Settings, Upload
from store import Store
from writer import SftpWriter

pytestmark = pytest.mark.integration

_USER, _PASSWORD, _REMOTE_DIR = "basic", "password", "upload"
_KEY_USER = "keypair"


@pytest.fixture(scope="module")
def _sftp_container():
    """Boot one atmoz/sftp server for the module with a password user and a key user."""
    from testcontainers.sftp import SFTPContainer, SFTPUser

    users = [
        SFTPUser(name=_USER, password=_PASSWORD, folders=[_REMOTE_DIR]),
        SFTPUser.with_keypair(name=_KEY_USER, folders=[_REMOTE_DIR]),
    ]
    with SFTPContainer(users=users) as c:
        yield c


@pytest.fixture(scope="module")
def sftp_server(_sftp_container):
    """(host, port) of the shared container."""
    return _sftp_container.get_container_host_ip(), _sftp_container.get_exposed_sftp_port()


@pytest.fixture(scope="module")
def keypair_user(_sftp_container):
    """The key-auth user; ``.private_key`` holds the generated PEM bytes."""
    return _sftp_container.users[1]


class _NoSleepClock(ClockInterface):
    """Clock stub for driving drain._tick without waiting: sleeps are no-ops."""

    def now(self, tz=None) -> datetime:
        return datetime.now(tz or timezone.utc)

    def perf_counter(self) -> float:
        return 0.0

    async def sleep(self, delay: float) -> None:
        return None

    def sleep_sync(self, delay: float) -> None:
        return None

    def advance(self, seconds: float) -> None:
        return None

    def set_time(self, dt: datetime) -> None:
        return None


def _password_client(host: str, port: int) -> paramiko.SSHClient:
    """A verification SSH client on the password user (agent/keys off so nothing ambient hijacks it)."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port, _USER, _PASSWORD, allow_agent=False, look_for_keys=False)
    return client


def _fresh_remote_dir(host: str, port: int, name: str) -> str:
    """Create a unique subdir under upload/ so each test only ever lists its own files."""
    remote = posixpath.join(_REMOTE_DIR, name)
    client = _password_client(host, port)
    try:
        client.open_sftp().mkdir(remote)
    finally:
        client.close()
    return remote


def _known_hosts_file(host: str, port: int, tmp_path) -> str:
    """Fetch the server's host key and write a known_hosts file paramiko will match for this port."""
    transport = paramiko.Transport((host, port))
    transport.connect()
    try:
        key = transport.get_remote_server_key()
    finally:
        transport.close()
    hostkeys = paramiko.HostKeys()
    hostname = host if port == 22 else f"[{host}]:{port}"
    hostkeys.add(hostname, key.get_name(), key)
    path = str(tmp_path / "known_hosts")
    hostkeys.save(path)
    return path


async def _store_with_rows() -> Store:
    store = Store(":memory:")
    await store.setup()
    ts = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    await store.append(ts, "pump-1", "temperature", 42.5)
    await store.append(ts, "pump-1", "status", "running")
    return store


async def _cfg(host: str, port: int, **over):
    return Settings(sftp={"host": host, "port": port, "username": _USER, "remote_dir": _REMOTE_DIR,
                          "auth": {"method": "password", "password": _PASSWORD}, **over}).sftp


@pytest.mark.asyncio
async def test_verified_host_upload_lands_on_server(sftp_server, tmp_path) -> None:
    """With the server's key in known_hosts, the batch uploads and the file is present on the server."""
    host, port = sftp_server
    cfg = await _cfg(host, port, verify_host_key=True, known_hosts=_known_hosts_file(host, port, tmp_path))
    store = await _store_with_rows()
    writer = SftpWriter(cfg, "csv")
    try:
        await writer.setup()
        result = await writer.write_batch(store, limit=1000)
    finally:
        await writer.teardown()
        await store.teardown()

    assert result.cursor is not None and result.n_rows == 2

    verify = paramiko.SSHClient()
    verify.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # password only: don't let a developer's running ssh-agent hijack this verification connection
    verify.connect(host, port, _USER, _PASSWORD, allow_agent=False, look_for_keys=False)
    try:
        listing = verify.open_sftp().listdir(_REMOTE_DIR)
    finally:
        verify.close()
    assert any(re.fullmatch(rf"batch-\d{{8}}T\d{{6}}-{result.cursor}\.csv", name) for name in listing)


@pytest.mark.asyncio
async def test_unknown_host_is_rejected(sftp_server, tmp_path) -> None:
    """The default (verify_host_key=True) rejects a server whose key isn't in known_hosts.
    The file holds a key for another host: non-empty (past the zero-keys fail-fast), but no
    match for this server, so RejectPolicy refuses the connection. Tested on _connection()
    directly: setup() classifies the resulting SSHException as transient and swallows it."""
    host, port = sftp_server
    hostkeys = paramiko.HostKeys()
    other = paramiko.ECDSAKey.generate()
    hostkeys.add("other.example.com", other.get_name(), other)
    path = str(tmp_path / "known_hosts")
    hostkeys.save(path)
    cfg = await _cfg(host, port, verify_host_key=True, known_hosts=path)
    writer = SftpWriter(cfg, "csv")
    try:
        with pytest.raises(paramiko.SSHException):
            await asyncio.to_thread(writer._connection)
    finally:
        await writer.teardown()


@pytest.mark.asyncio
async def test_empty_known_hosts_fails_fast(sftp_server, tmp_path) -> None:
    """verify_host_key=True with zero loaded keys fails setup with guidance, before connecting."""
    host, port = sftp_server
    empty = str(tmp_path / "empty_known_hosts")
    open(empty, "w").close()
    cfg = await _cfg(host, port, verify_host_key=True, known_hosts=empty)
    writer = SftpWriter(cfg, "csv")
    try:
        with pytest.raises(RuntimeError, match="no host keys were loaded"):
            await writer.setup()
    finally:
        await writer.teardown()


@pytest.mark.asyncio
async def test_parquet_upload_is_complete_and_atomic(sftp_server, tmp_path) -> None:
    """PARQUET (the default, truncation-sensitive format) lands whole under its final name, with
    no leftover .part: download it back, read it, and assert both rows' payloads survived. This
    exercises the atomic temp-then-rename guarantee for real (the unit tests only fake it)."""
    host, port = sftp_server
    remote = _fresh_remote_dir(host, port, "parquet")
    cfg = await _cfg(host, port, verify_host_key=False, remote_dir=remote)
    store = await _store_with_rows()
    writer = SftpWriter(cfg, "parquet")
    try:
        await writer.setup()
        result = await writer.write_batch(store, limit=1000)
    finally:
        await writer.teardown()
        await store.teardown()

    assert result.cursor is not None and result.n_rows == 2

    local = str(tmp_path / "roundtrip.parquet")
    client = _password_client(host, port)
    try:
        sftp = client.open_sftp()
        listing = sftp.listdir(remote)
        uploaded = [n for n in listing
                    if re.fullmatch(rf"batch-\d{{8}}T\d{{6}}-{result.cursor}\.parquet", n)]
        assert len(uploaded) == 1                        # exactly the published file
        assert not [n for n in listing if re.fullmatch(r"\..*\.part", n)]  # no temp .part left behind
        sftp.get(posixpath.join(remote, uploaded[0]), local)
    finally:
        client.close()

    # Read the downloaded Parquet back: a truncated footer would make this raise.
    rows = duckdb.connect().execute(
        f"SELECT datastream, CAST(payload AS VARCHAR) FROM read_parquet('{local}') ORDER BY datastream"
    ).fetchall()
    assert rows == [("status", '"running"'), ("temperature", "42.5")]


@pytest.mark.asyncio
async def test_reconnect_after_dropped_transport(sftp_server) -> None:
    """A dropped connection is not fatal: the failing batch raises (and _resets the dead session),
    then a further batch rebuilds the session and lands a second file. The store is never dropped
    here (that's the drain's job), so every write_batch re-reads the same two rows."""
    host, port = sftp_server
    remote = _fresh_remote_dir(host, port, "reconnect")
    cfg = await _cfg(host, port, verify_host_key=False, remote_dir=remote)
    store = await _store_with_rows()
    writer = SftpWriter(cfg, "csv")
    try:
        await writer.setup()
        first = await writer.write_batch(store, limit=1000)
        assert first.n_rows == 2

        # Kill the live transport underneath the cached client; the next upload must fail.
        dead = writer._client.get_transport()
        dead.close()
        with pytest.raises(Exception):
            await writer.write_batch(store, limit=1000)
        assert writer._sftp is None                      # _reset dropped the dead session

        # A further batch rebuilds the connection from scratch and lands another file.
        third = await writer.write_batch(store, limit=1000)
        assert third.n_rows == 2
        # A genuinely fresh session, not the transport we just closed.
        rebuilt = writer._client.get_transport()
        assert rebuilt is not dead and rebuilt.is_active()
    finally:
        await writer.teardown()
        await store.teardown()

    # The batch name is timestamp+cursor; the first and post-reconnect uploads can share a
    # (same-second, same-cursor) name, so assert a completed file landed, not a count.
    client = _password_client(host, port)
    try:
        listing = client.open_sftp().listdir(remote)
    finally:
        client.close()
    assert any(name.startswith("batch-") for name in listing)
    assert not [n for n in listing if re.fullmatch(r"\..*\.part", n)]  # failed attempt left no .part


@pytest.mark.asyncio
async def test_private_key_auth_uploads(sftp_server, keypair_user) -> None:
    """A real private-key handshake: configure method=private_key with the generated PEM and
    assert a key-exchange auth uploads a file (exercises _load_pkey + paramiko key auth on the wire)."""
    host, port = sftp_server
    pem = keypair_user.private_key.decode()
    cfg = await _cfg(host, port, verify_host_key=False, username=_KEY_USER,
                     auth={"method": "private_key", "private_key": pem})
    store = await _store_with_rows()
    writer = SftpWriter(cfg, "csv")
    try:
        await writer.setup()
        result = await writer.write_batch(store, limit=1000)
    finally:
        await writer.teardown()
        await store.teardown()

    assert result.cursor is not None and result.n_rows == 2

    verify = paramiko.SSHClient()
    verify.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = paramiko.RSAKey.from_private_key(io.StringIO(pem))
    verify.connect(host, port, _KEY_USER, pkey=pkey, allow_agent=False, look_for_keys=False)
    try:
        listing = verify.open_sftp().listdir(_REMOTE_DIR)     # keypair user's own home
    finally:
        verify.close()
    assert any(re.fullmatch(rf"batch-\d{{8}}T\d{{6}}-{result.cursor}\.csv", name) for name in listing)


@pytest.mark.asyncio
async def test_drain_tick_uploads_then_drops(sftp_server) -> None:
    """A real drain cycle end to end: _tick writes the batch, and only after the file lands does
    it drop the buffer. No-op clock so the post-cycle sleep doesn't wait."""
    host, port = sftp_server
    remote = _fresh_remote_dir(host, port, "drain")
    cfg = await _cfg(host, port, verify_host_key=False, remote_dir=remote)
    store = await _store_with_rows()
    writer = SftpWriter(cfg, "parquet")
    try:
        await writer.setup()
        r = await _tick(writer, store, Upload(), Buffer(), _NoSleepClock())
        assert r is not None and r.n_rows == 2
        assert await store.count() == 0                  # dropped, but only after the upload landed
    finally:
        await writer.teardown()
        await store.teardown()

    client = _password_client(host, port)
    try:
        listing = client.open_sftp().listdir(remote)
    finally:
        client.close()
    assert any(name.startswith("batch-") for name in listing)


@pytest.mark.asyncio
async def test_missing_remote_dir_fails_setup(sftp_server) -> None:
    """A remote_dir that doesn't exist is a deployment error: setup's stat check raises
    FileNotFoundError rather than silently buffering forever."""
    host, port = sftp_server
    cfg = await _cfg(host, port, verify_host_key=False, remote_dir="upload/does-not-exist")
    writer = SftpWriter(cfg, "csv")
    try:
        with pytest.raises(FileNotFoundError):
            await writer.setup()
    finally:
        await writer.teardown()
