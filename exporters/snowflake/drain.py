from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol, Union

from kelvin.application.clock import ClockInterface
from kelvin.logs import logger

from store import FileRecords, Records, Store

if TYPE_CHECKING:                       # shared module: needs only the attribute shapes at runtime
    from settings import Buffer, Retry, Upload

Read = Union[Records, FileRecords]      # both carry .cursor and .n_rows


class Writer(Protocol):
    # Each writer carries its own `fmt` (None for record sinks, else the file format), but the
    # drain loop never reads it (internal to write_batch), so it's not part of the contract.
    async def setup(self) -> None: ...
    async def write_batch(self, store: Store, limit: int) -> Read: ...
    async def teardown(self) -> None: ...


async def drain(writer: Writer, store: Store, upload: "Upload", buffer: "Buffer",
                clock: ClockInterface) -> None:
    """SDK-managed @app.task. A failed tick is logged and retried next interval rather
    than killing the task; cancel/await on disconnect is the SDK's job. Sleeps go through
    the app clock so KelvinAppTest's VirtualClock can fast-forward them. Resource teardown
    (writer + store) is owned by main's on_disconnect, not this loop."""
    while True:
        try:
            await _tick(writer, store, upload, buffer, clock)
        except Exception as e:               # CancelledError isn't an Exception: cancel still propagates
            logger.error("Drain tick failed; retrying next interval", error=str(e))
            await clock.sleep(upload.interval)


async def _tick(writer: Writer, store: Store, upload: "Upload", buffer: "Buffer",
                clock: ClockInterface) -> None:
    """One cycle (extracted so it's testable without the infinite loop): write a batch,
    drop only on success, always cap, sleep unless the batch was full."""
    r = await _attempt(lambda: writer.write_batch(store, upload.batch_size), upload.retry, clock)
    if r and r.cursor is not None:
        await store.drop(r.cursor)               # only after a successful send
    await store.cap(buffer.max_backlog)          # disk-safety guard every tick
    if r and r.n_rows == upload.batch_size:
        return                                   # backlog: loop drains again, no sleep
    await clock.sleep(upload.interval)           # virtualized under KelvinAppTest


async def _attempt(op, retry: "Retry", clock: ClockInterface) -> Optional[Read]:
    """Run ``op``, absorbing failures with exponential backoff capped at retry.max_delay.
    Returns the result, or None after exhausting retry.attempts (the batch stays buffered and is retried on the next tick)."""
    for n in range(retry.attempts):
        try:
            return await op()
        except Exception as e:
            if n < retry.attempts - 1:               # more attempts left: back off and retry
                logger.warning("Upload attempt failed, retrying", attempt=n + 1, error=str(e))
                await clock.sleep(min(retry.base_delay * 2 ** n, retry.max_delay))
            else:                                    # attempts exhausted for this tick
                logger.error("Upload failed; batch stays buffered, retrying next interval",
                             attempts=retry.attempts, error=str(e))
    return None
