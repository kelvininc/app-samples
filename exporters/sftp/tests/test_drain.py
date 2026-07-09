"""Unit tests for the shared drain loop and retry helper."""
import asyncio

import pytest

import drain
from store import Records

pytestmark = pytest.mark.asyncio


class RecordingClock:
    """Minimal ClockInterface stand-in that records sleep delays instead of waiting."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.slept.append(delay)


class Retry:
    def __init__(self, attempts: int = 3, base_delay: float = 1.0, max_delay: float = 30.0) -> None:
        self.attempts, self.base_delay, self.max_delay = attempts, base_delay, max_delay


class Upload:
    def __init__(self, batch_size: int = 100, interval: int = 60, retry: Retry | None = None) -> None:
        self.batch_size, self.interval, self.retry = batch_size, interval, retry or Retry(attempts=1)


class Buffer:
    def __init__(self, max_backlog: int = 0) -> None:
        self.max_backlog = max_backlog


class TestAttempt:
    """The retry-with-backoff helper."""

    async def test_returns_result_after_transient_failures(self) -> None:
        """_attempt retries a flaky op and returns the first success."""
        calls = {"n": 0}

        async def op() -> Records:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("boom")
            return Records([], 7, 5, 0)

        result = await drain._attempt(op, Retry(attempts=3, base_delay=0), RecordingClock())
        assert result.cursor == 7 and calls["n"] == 3

    async def test_returns_none_after_exhausting_attempts(self) -> None:
        """_attempt gives up and returns None once attempts run out."""

        async def op() -> Records:
            raise RuntimeError("boom")

        assert await drain._attempt(op, Retry(attempts=3, base_delay=0), RecordingClock()) is None

    async def test_backoff_is_exponential_and_capped(self) -> None:
        """_attempt backs off 1, 2, 4, ... clamped at max_delay between attempts, and
        never sleeps after the final attempt (5 attempts -> 4 backoffs)."""
        clock = RecordingClock()

        async def op() -> Records:
            raise RuntimeError("boom")

        await drain._attempt(op, Retry(attempts=5, base_delay=1, max_delay=4), clock)
        assert clock.slept == [1, 2, 4, 4]   # 8 clamped to 4; no sleep after attempt 5


class _FakeStore:
    def __init__(self) -> None:
        self.drops: list[int] = []
        self.caps: list[int] = []

    async def drop(self, cursor: int) -> None:
        self.drops.append(cursor)

    async def cap(self, max_backlog: int) -> None:
        self.caps.append(max_backlog)

    async def count(self) -> int:
        return 0                                    # backlog for the give-up log; not asserted here


class TestTick:
    """A single drain cycle."""

    async def test_failed_send_skips_drop_but_still_caps(self) -> None:
        """When the write fails, the batch is not dropped but the backlog cap still runs."""
        class FailingWriter:
            async def write_batch(self, store, limit) -> Records:
                raise RuntimeError("down")
            async def teardown(self) -> None: ...

        store = _FakeStore()
        await drain._tick(FailingWriter(), store, Upload(), Buffer(max_backlog=500), RecordingClock())
        assert store.drops == [] and store.caps == [500]

    async def test_successful_send_drops_at_cursor_and_caps(self) -> None:
        """A successful write drops up to the returned cursor and runs the cap."""
        class OkWriter:
            async def write_batch(self, store, limit) -> Records:
                return Records([], 42, 100, 0)        # count == batch_size
            async def teardown(self) -> None: ...

        store = _FakeStore()
        await drain._tick(OkWriter(), store, Upload(batch_size=100), Buffer(), RecordingClock())
        assert store.drops == [42] and store.caps == [0]

    async def test_full_batch_skips_sleep(self) -> None:
        """A full batch returns early so the loop drains the backlog without sleeping."""
        class OkWriter:
            async def write_batch(self, store, limit) -> Records:
                return Records([], 42, 100, 0)        # == batch_size
            async def teardown(self) -> None: ...

        clock = RecordingClock()
        await drain._tick(OkWriter(), _FakeStore(), Upload(batch_size=100, interval=60), Buffer(), clock)
        assert clock.slept == []                   # no sleep on a full batch

    async def test_partial_batch_sleeps_one_interval(self) -> None:
        """A non-full batch sleeps exactly one upload interval before the next tick."""
        class OkWriter:
            async def write_batch(self, store, limit) -> Records:
                return Records([], 9, 10, 0)          # 10 < batch_size 100
            async def teardown(self) -> None: ...

        clock = RecordingClock()
        await drain._tick(OkWriter(), _FakeStore(), Upload(batch_size=100, interval=60), Buffer(), clock)
        assert clock.slept == [60]


class TestDrainLoop:
    """The long-running drain() loop and its (lack of) teardown responsibility."""

    async def test_loops_until_cancelled_without_tearing_down(self) -> None:
        """drain() ticks repeatedly and, on cancellation, propagates cleanly without closing
        the writer; teardown is main's on_disconnect job now, not the loop's."""
        ticks, teardowns = {"n": 0}, {"n": 0}

        class CountingWriter:
            async def write_batch(self, store, limit) -> Records:
                ticks["n"] += 1
                return Records([], None, 0, 0)            # empty -> no drop, loop sleeps each tick
            async def teardown(self) -> None:
                teardowns["n"] += 1

        class YieldClock:
            async def sleep(self, delay: float) -> None:
                await asyncio.sleep(0)                 # yield so the task stays cancellable

        task = asyncio.create_task(
            drain.drain(CountingWriter(), _FakeStore(), Upload(interval=1), Buffer(), YieldClock())
        )
        while ticks["n"] < 3:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ticks["n"] >= 3 and teardowns["n"] == 0   # loop never owns teardown

    async def test_tick_failure_is_absorbed_and_loop_continues(self) -> None:
        """A tick that raises outside the retry helper (e.g. store.drop/cap) is logged, sleeps one
        interval, and the loop keeps running; cancellation still propagates."""
        caps = {"n": 0}

        class ExplodingStore(_FakeStore):
            async def cap(self, max_backlog: int) -> None:
                caps["n"] += 1
                raise RuntimeError("cap failed")

        class OkWriter:
            async def write_batch(self, store, limit) -> Records:
                return Records([], None, 0, 0)
            async def teardown(self) -> None: ...

        class YieldRecordingClock:
            def __init__(self) -> None:
                self.slept: list[float] = []
            async def sleep(self, delay: float) -> None:
                self.slept.append(delay)
                await asyncio.sleep(0)                 # yield so the task stays cancellable

        clock = YieldRecordingClock()
        task = asyncio.create_task(
            drain.drain(OkWriter(), ExplodingStore(), Upload(interval=7), Buffer(), clock)
        )
        while caps["n"] < 3:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert caps["n"] >= 3                          # tick failures didn't kill the loop
        assert set(clock.slept) == {7}                 # guard slept one interval per failure


class TestRecovery:
    """The drain loop tracks failure streaks and marks recovery."""

    async def test_recovered_logged_once_after_failed_ticks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two failed cycles then a delivered batch log exactly one 'Upload recovered' with the
        streak length and remaining backlog; the following routine batch logs no recovery."""
        infos: list[dict] = []
        monkeypatch.setattr(drain.logger, "info", lambda msg, **kw: infos.append({"msg": msg, **kw}))
        outcomes: list = [RuntimeError("down"), RuntimeError("down"),
                          Records([], 7, 5, 3), Records([], 9, 5, 0)]

        class FlakyWriter:
            async def write_batch(self, store, limit) -> Records:
                out = outcomes.pop(0)
                if isinstance(out, Exception):
                    raise out
                return out
            async def teardown(self) -> None: ...

        class StopClock(RecordingClock):
            async def sleep(self, delay: float) -> None:
                if not outcomes:                     # script ran dry: end the loop
                    raise asyncio.CancelledError
                await asyncio.sleep(0)

        with pytest.raises(asyncio.CancelledError):
            await drain.drain(FlakyWriter(), _FakeStore(),
                              Upload(retry=Retry(attempts=1, base_delay=0)), Buffer(), StopClock())
        recoveries = [i for i in infos if i["msg"] == "Upload recovered"]
        assert recoveries == [{"msg": "Upload recovered", "failed_ticks": 2, "backlog": 3}]

    async def test_failure_streak_counts_consecutive_cycles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every failed cycle logs an error carrying the running consecutive_failures count."""
        errors: list[dict] = []
        monkeypatch.setattr(drain.logger, "error", lambda msg, **kw: errors.append(kw))

        class FailingWriter:
            async def write_batch(self, store, limit) -> Records:
                raise RuntimeError("down")
            async def teardown(self) -> None: ...

        class CountingClock(RecordingClock):
            async def sleep(self, delay: float) -> None:
                self.slept.append(delay)
                if len(self.slept) >= 3:
                    raise asyncio.CancelledError
                await asyncio.sleep(0)

        with pytest.raises(asyncio.CancelledError):
            await drain.drain(FailingWriter(), _FakeStore(),
                              Upload(retry=Retry(attempts=1, base_delay=0)), Buffer(), CountingClock())
        assert [e["consecutive_failures"] for e in errors] == [1, 2, 3]
