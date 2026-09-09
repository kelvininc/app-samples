import asyncio
import io
import os
import posixpath
import tempfile
from datetime import datetime, timezone
from typing import Optional

import paramiko
from kelvin.logs import logger

from settings import Sftp
from store import FileRecords, Store


def _load_pkey(pem: str, passphrase: Optional[str]) -> paramiko.PKey:
    """Load a PEM private key, trying the common key types (paramiko has no generic auto-loader)."""
    last_exc: Optional[Exception] = None
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(pem), password=passphrase)
        except paramiko.SSHException as e:
            last_exc = e
    raise last_exc or paramiko.SSHException("could not load private key")


class SftpWriter:
    """File sink: stream a batch from the buffer to a temp file, then SFTP-upload it.

    paramiko is synchronous, so the blocking calls run in asyncio.to_thread. The SSH/SFTP
    connection is built once and reused; a failed upload drops it and raises so the drain
    retries the whole batch (the buffer isn't trimmed until the file lands).
    """

    def __init__(self, cfg: Sftp, fmt: str):
        self.cfg = cfg
        self.fmt = fmt              # "parquet"|"csv"|"json"; drives store.read_to_file
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    def _connection(self) -> paramiko.SFTPClient:
        if self._sftp is not None:
            return self._sftp

        client = paramiko.SSHClient()
        if self.cfg.verify_host_key:
            if self.cfg.known_hosts:
                try:
                    client.load_host_keys(self.cfg.known_hosts)
                except OSError as e:
                    # Missing/unreadable known_hosts is a deployment error, never heals by
                    # retrying; RuntimeError puts it in _validate's fail-fast tuple.
                    raise RuntimeError(
                        f"could not read sftp.known_hosts file '{self.cfg.known_hosts}': {e}"
                    ) from e
            else:
                client.load_system_host_keys()
            if not client.get_host_keys():
                # RejectPolicy with zero known keys can never connect; fail with a fix, not
                # a generic reject at connect time.
                raise RuntimeError(
                    "verify_host_key is enabled but no host keys were loaded: set sftp.known_hosts "
                    "to a known_hosts file deployed with the app (e.g. a platform text volume), or "
                    "set verify_host_key: false for local development (MITM risk)"
                )
            client.set_missing_host_key_policy(paramiko.RejectPolicy())     # fail on unknown host (anti-MITM)
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())    # dev only

        t = self.cfg.timeout
        try:                                                                # close the client on any failure
            a = self.cfg.auth
            if a.method == "password":
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.password is None:
                    raise RuntimeError("auth.method='password' but no password configured")
                # allow_agent/look_for_keys off: authenticate only with the configured credential,
                # never an ambient SSH agent or the host's ~/.ssh keys (wrong identity / confusing fails).
                client.connect(hostname=self.cfg.host, port=self.cfg.port, username=self.cfg.username,
                               password=a.password.get_secret_value(), allow_agent=False, look_for_keys=False,
                               timeout=t, banner_timeout=t, auth_timeout=t)
            else:
                # Guaranteed by Settings' one-auth validator; explicit raise survives python -O.
                if a.private_key is None:
                    raise RuntimeError("auth.method='private_key' but no private_key configured")
                passphrase = a.private_key_passphrase.get_secret_value() if a.private_key_passphrase else None
                pkey = _load_pkey(a.private_key.get_secret_value(), passphrase)
                # allow_agent/look_for_keys off: authenticate only with the configured key,
                # never an ambient SSH agent or the host's ~/.ssh keys (wrong identity / confusing fails).
                client.connect(hostname=self.cfg.host, port=self.cfg.port, username=self.cfg.username,
                               pkey=pkey, allow_agent=False, look_for_keys=False,
                               timeout=t, banner_timeout=t, auth_timeout=t)

            sftp = client.open_sftp()
            channel = sftp.get_channel()
            if channel is not None:                     # None-guard keeps stubbed test clients working
                channel.settimeout(t)                   # bounds put()/stat() socket ops, not just connect
        except Exception:
            client.close()                              # never leak a connected transport
            raise

        self._client = client
        self._sftp = sftp
        return sftp

    async def setup(self) -> None:
        await asyncio.to_thread(self._validate)         # fail fast on deterministic config errors

    def _validate(self) -> None:
        # Setup policy: deterministic config errors (bad credentials, no host keys loaded,
        # host-key mismatch, missing remote_dir) crash the app so a misdeployment is loud.
        # Transient errors (timeout, refused/unreachable, network SSHException) and anything
        # unclassified are logged and swallowed: the buffer accumulates and the drain retries.
        # An unknown server key under RejectPolicy is also swallowed (the operator may still be
        # deploying the known_hosts volume) but logged with explicit host-key wording.
        try:
            sftp = self._connection()
        except (paramiko.AuthenticationException, paramiko.BadHostKeyException, RuntimeError):
            # BadHostKeyException = the server's key differs from the pinned one; the MITM /
            # rotated-key signal host verification exists for; never heals by retrying.
            raise                                       # config error (RuntimeError = host-key fail-fast)
        except paramiko.SSHException as e:
            # RejectPolicy raises a plain SSHException ("Server '...' not found in known_hosts"),
            # so the message is the only way to tell an unknown-key rejection apart from a
            # network-level SSH failure. Both warn and return.
            if "not found in known_hosts" in str(e):
                logger.warning("Host key verification failed: server key not in known_hosts; "
                               "buffering and retrying", host=self.cfg.host, error=str(e))
            else:
                logger.warning("SFTP unreachable at setup; buffering and retrying",
                               host=self.cfg.host, error=str(e), error_type=type(e).__name__)
            return
        except Exception as e:
            logger.warning("SFTP unreachable at setup; buffering and retrying", host=self.cfg.host,
                           error=str(e), error_type=type(e).__name__)
            return
        try:
            sftp.stat(self.cfg.remote_dir)              # proves the remote dir exists/reachable
        except (FileNotFoundError, PermissionError):
            raise                                       # remote_dir missing/inaccessible: config error
        except Exception as e:                          # timeout, dropped connection, anything unclassified
            logger.warning("SFTP setup check failed; buffering and retrying", host=self.cfg.host,
                           error=str(e), error_type=type(e).__name__)
            return
        logger.info("SFTP writer ready", host=self.cfg.host, remote_dir=self.cfg.remote_dir,
                    auth=self.cfg.auth.method, format=self.fmt)

    async def write_batch(self, store: Store, limit: int) -> FileRecords:
        # File sink: COPY the batch to a temp file (streamed to disk), upload it, then always
        # delete the temp file.
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
        # Timestamp + cursor: names stay collision-free even if the buffer DB (and its seq)
        # is ever recreated. At-least-once delivery: a retry after a crash between upload and
        # drop re-sends the batch under a fresh timestamp, so consumers must tolerate duplicates.
        stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
        name = f"batch-{stamp}-{r.cursor}.{self.fmt}"
        remote = posixpath.join(self.cfg.remote_dir, name)
        # Upload to a hidden .part sibling first, then atomically rename into the final name, so a
        # consumer polling remote_dir never reads a half-written file (a truncated Parquet footer
        # is unreadable). Timestamp+cursor keep the temp name collision-free too.
        tmp_remote = posixpath.join(self.cfg.remote_dir, f".{name}.part")
        sftp = self._connection()
        try:
            sftp.put(path, tmp_remote)                  # streams from disk
            self._rename(sftp, tmp_remote, remote)      # publish atomically
        except Exception:
            self._cleanup_remote(sftp, tmp_remote)      # best-effort: don't leave a .part behind
            self._reset()                               # drop a possibly-stale connection; retry rebuilds
            raise
        logger.info("Uploaded to SFTP", rows=r.n_rows, backlog=r.backlog,
                    host=self.cfg.host, remote=remote)

    @staticmethod
    def _rename(sftp: paramiko.SFTPClient, src: str, dst: str) -> None:
        # posix_rename (the posix-rename@openssh.com extension) renames atomically; plain rename()
        # is the fallback for servers that lack the extension. Names carry timestamp+cursor, so the
        # target never pre-exists and the non-atomic fallback is safe (no overwrite race).
        try:
            sftp.posix_rename(src, dst)
        except (AttributeError, IOError, paramiko.SSHException):
            sftp.rename(src, dst)

    @staticmethod
    def _cleanup_remote(sftp: paramiko.SFTPClient, remote: str) -> None:
        # Best-effort: a failed put/rename can leave a .part behind. Try to remove it, but never
        # mask the original upload error (the connection may already be gone).
        try:
            sftp.remove(remote)
        except FileNotFoundError:
            pass                                        # nothing to clean up (put failed before the .part existed)
        except Exception as e:
            logger.warning("Failed to remove partial SFTP upload", remote=remote, error=str(e))

    def _reset(self) -> None:
        sftp, self._sftp = self._sftp, None
        client, self._client = self._client, None
        for conn in (sftp, client):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.warning("Failed to close stale SFTP connection")

    async def teardown(self) -> None:
        await asyncio.to_thread(self._reset)
