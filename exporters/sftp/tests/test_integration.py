"""Real-server smoke tests (a live SFTP server via testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. These drive the real
`SftpWriter` (paramiko over the wire) against an `atmoz/sftp` container, covering the two things
the unit tests can only fake: an actual file upload, and host-key verification; the security
default that rejects an unknown host (anti-MITM).
"""
import asyncio
import re
from datetime import datetime, timezone

import paramiko
import pytest

from settings import Settings
from store import Store
from writer import SftpWriter

pytestmark = pytest.mark.integration

_USER, _PASSWORD, _REMOTE_DIR = "basic", "password", "upload"


@pytest.fixture(scope="module")
def sftp_server():
    """Boot one atmoz/sftp server for the module; yields (host, port)."""
    from testcontainers.sftp import SFTPContainer, SFTPUser

    with SFTPContainer(users=[SFTPUser(name=_USER, password=_PASSWORD, folders=[_REMOTE_DIR])]) as c:
        yield c.get_container_host_ip(), c.get_exposed_sftp_port()


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
