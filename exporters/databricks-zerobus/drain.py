from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Optional, Protocol, Union

from kelvin.application.clock import ClockInterface
from kelvin.logs import logger

from store import FileRecords, Records, Store

if TYPE_CHECKING:                       # shared module: needs only the attribute shapes at runtime
    from settings import Buffer, Retry, Upload

Read = Union[Records, FileRecords]      # both carry .cursor, .n_rows and .backlog


class Writer(Protocol):
    # Each writer carries its own `fmt` (None for record sinks, else the file format), but the
    # drain loop never reads it (internal to write_batch), so it's not part of the contract.
    async def setup(self) -> None: ...
    async def write_batch(self, store: Store, limit: int) -> Read: ...
    async def teardown(self) -> None: ...


async def drain(writer: Writer, store: Store, upload: Upload, buffer: Buffer,
                clock: ClockInterface) -> None:
    """SDK-managed @app.task. A failed cycle is logged and retried next interval rather
    than killing the task; cancel/await on disconnect is the SDK's job. Sleeps go through
    the app clock so KelvinAppTest's VirtualClock can fast-forward them. Resource teardown
    (writer + store) is owned by main's on_disconnect, not this loop.

    Tracks consecutive failed cycles so an incident has a visible end: the first
    successful upload after failures logs "Upload recovered"."""
    failed_ticks = 0
    while True:
        try:
            r = await _tick(writer, store, upload, buffer, clock, failed_ticks)
        except Exception as e:      # CancelledError is BaseException: cancellation still propagates
            failed_ticks += 1
            logger.error("Export cycle failed; retrying next interval",
                         consecutive_failures=failed_ticks, error=str(e), error_type=type(e).__name__)
            await clock.sleep(upload.interval)
            continue
        if r is None:                       # upload exhausted its retries; batch stays buffered
            failed_ticks += 1
        elif r.n_rows:                      # a delivered batch ends any failure streak
            failed_ticks = 0


async def _tick(writer: Writer, store: Store, upload: Upload, buffer: Buffer,
                clock: ClockInterface, failed_ticks: int = 0) -> Optional[Read]:
    """One cycle (extracted so it's testable without the infinite loop): write a batch,
    drop only on success, always cap, sleep unless the batch was full. ``failed_ticks``
    is the caller's count of consecutive failed cycles, passed in for log context only."""
    r = await _attempt(lambda: writer.write_batch(store, upload.batch_size), upload.retry, clock)
    if r is None:
        logger.error("Upload failed; batch stays buffered until next interval",
                     attempts=upload.retry.attempts, consecutive_failures=failed_ticks + 1,
                     backlog=await store.count())
    else:
        if r.cursor is not None:
            await store.drop(r.cursor)           # only after a successful send
        if failed_ticks and r.n_rows:
            logger.info("Upload recovered", failed_ticks=failed_ticks, backlog=r.backlog)
    await store.cap(buffer.max_backlog)          # disk-safety guard every tick
    if r and r.n_rows == upload.batch_size:
        return r                                 # backlog: loop drains again, no sleep
    await clock.sleep(upload.interval)           # virtualized under KelvinAppTest
    return r


async def _attempt(op: Callable[[], Awaitable[Read]], retry: Retry,
                   clock: ClockInterface) -> Optional[Read]:
    """Run ``op``, absorbing failures with exponential backoff capped at retry.max_delay.
    Returns the result, or None after exhausting retry.attempts (the caller logs the
    give-up summary with backlog context)."""
    for n in range(retry.attempts):
        try:
            return await op()
        except Exception as e:
            # error_type always identifies the failure: str(e) is empty for some
            # exceptions (TimeoutError, ConnectionError, certain SSLErrors).
            if n == retry.attempts - 1:              # final attempt: no backoff, give up
                logger.warning("Upload attempt failed",
                               attempt=f"{n + 1}/{retry.attempts}",
                               error=str(e), error_type=type(e).__name__)
                break
            delay = min(retry.base_delay * 2 ** n, retry.max_delay)
            logger.warning("Upload attempt failed, retrying",
                           attempt=f"{n + 1}/{retry.attempts}", retry_in=delay,
                           error=str(e), error_type=type(e).__name__)
            await clock.sleep(delay)
    return None
