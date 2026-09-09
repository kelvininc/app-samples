"""Unit tests for the camera connector's pure helpers (encode, listing, mapping) and loop cursor."""
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import main


def _write_png(path: Path, size=(4, 3)) -> str:
    Image.new("RGB", size, color=(10, 20, 30)).save(path, format="PNG")
    return str(path)


def _assets(mapping: dict) -> dict:
    """Build a fake `app.assets`: {asset: {stream: configuration_dict}}."""
    return {
        asset: SimpleNamespace(
            datastreams={s: SimpleNamespace(configuration=cfg) for s, cfg in streams.items()}
        )
        for asset, streams in mapping.items()
    }


def _recording_publish(*, key=lambda msg: msg, result=True):
    """Build a fake `app.publish` that records each message and returns `result`.

    `key` selects what to record per message (the whole msg by default, or e.g.
    `lambda m: m.payload["image_filename"]`). Returns `(recorded, publish_fn)`.
    """
    recorded: list = []

    async def publish(msg) -> bool:
        recorded.append(key(msg))
        return result

    return recorded, publish


class TestEncodeImage:
    def test_encodes_to_base64_payload(self, tmp_path: Path) -> None:
        path = _write_png(tmp_path / "frame.png", size=(4, 3))
        payload = main.encode_image(path)
        assert payload["image_filename"] == "frame.png"
        assert payload["image_format"] == "PNG"
        assert payload["image_size"] == {"width": 4, "height": 3}

    def test_payload_carries_exact_source_bytes(self, tmp_path: Path) -> None:
        """No re-encode: the base64 payload decodes to the file's bytes, byte for byte."""
        path = Path(_write_png(tmp_path / "frame.png"))
        payload = main.encode_image(str(path))
        assert base64.b64decode(payload["image_base64"]) == path.read_bytes()


class TestImageFiles:
    def test_lists_only_images_sorted(self, tmp_path: Path) -> None:
        _write_png(tmp_path / "b.png")
        _write_png(tmp_path / "a.jpg")
        _write_png(tmp_path / "c.JPEG")  # extension match is case-insensitive
        (tmp_path / "notes.txt").write_text("x")
        (tmp_path / ".DS_Store").write_bytes(b"\x00")
        (tmp_path / "sub").mkdir()
        assert [Path(p).name for p in main.image_files(str(tmp_path))] == ["a.jpg", "b.png", "c.JPEG"]

    def test_non_images_are_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "b.txt").write_text("x")
        (tmp_path / "a.txt").write_text("x")
        assert main.image_files(str(tmp_path)) == []

    def test_missing_dir_is_empty(self, tmp_path: Path) -> None:
        assert main.image_files(str(tmp_path / "nope")) == []


class TestBuildTargets:
    def test_uses_default_dir_when_unset(self) -> None:
        assets = _assets({"line-1": {"cam": {}}})
        assert main.build_targets(assets, "images") == [("line-1", "cam", "images")]

    def test_per_stream_source_dir_override(self) -> None:
        assets = _assets({"line-1": {"cam": {"source_dir": "/data/cam1"}}})
        assert main.build_targets(assets, "images") == [("line-1", "cam", "/data/cam1")]

    def test_multiple_streams(self) -> None:
        assets = _assets({"a": {"c1": {}}, "b": {"c2": {"source_dir": "/x"}}})
        assert sorted(main.build_targets(assets, "images")) == [("a", "c1", "images"), ("b", "c2", "/x")]


class _StopLoop(Exception):
    """Raised by the fake sleep to end main()'s infinite loop after N cycles."""


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    *,
    assets: dict,
    app_configuration: dict,
    publish,
    max_cycles: int = 1,
) -> list[float]:
    """Run main() with a fake app, stopping after `max_cycles` sleeps.

    Returns the list of durations passed to asyncio.sleep, so callers can assert
    which wait path (default interval vs configured publish_interval) was taken.
    """
    slept: list[float] = []

    async def fake_connect() -> None:
        pass

    async def fake_sleep(seconds) -> None:
        slept.append(seconds)
        if len(slept) >= max_cycles:
            raise _StopLoop

    fake_app = SimpleNamespace(
        connect=fake_connect,
        publish=publish,
        assets=assets,
        app_configuration=app_configuration,
    )
    monkeypatch.setattr(main, "app", fake_app)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        asyncio.run(main.main())
    return slept


class TestInvalidConfig:
    def test_logs_and_sleeps_default_interval_without_publishing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        published, fake_publish = _recording_publish()

        slept = _run_main(
            monkeypatch,
            assets=_assets({"line-1": {"cam": {}}}),
            app_configuration={"camera": {"publish_interval": 0}},  # ge=1 violated
            publish=fake_publish,
        )

        assert published == []
        assert slept == [main._DEFAULT_INTERVAL]
        assert "Invalid configuration" in capsys.readouterr().out


class TestNoStreamsMapped:
    def test_warns_and_sleeps_publish_interval(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        published, fake_publish = _recording_publish()

        slept = _run_main(
            monkeypatch,
            assets={},  # nothing mapped -> build_targets returns []
            app_configuration={"camera": {"images_dir": "images", "publish_interval": 7}},
            publish=fake_publish,
        )

        assert published == []
        assert slept == [7]  # configured interval, not the invalid-config fallback
        assert "No camera streams mapped" in capsys.readouterr().out


class TestEmptySourceDir:
    def test_warns_and_skips_target_without_publishing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # tmp_path exists but holds no supported images -> image_files() returns [].
        published, fake_publish = _recording_publish()

        slept = _run_main(
            monkeypatch,
            assets=_assets({"line-1": {"cam": {}}}),
            app_configuration={"camera": {"images_dir": str(tmp_path), "publish_interval": 5}},
            publish=fake_publish,
        )

        # The empty target is skipped, nothing is published, and the cycle still sleeps.
        assert published == []
        assert slept == [5]
        assert "No supported images in source directory" in capsys.readouterr().out


class TestEncodeFailureSkip:
    def test_bad_image_is_skipped_and_loop_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # "bad.png" sorts before "good.png": cycle 1 hits the corrupt file, cycle 2 the valid one.
        (tmp_path / "bad.png").write_bytes(b"not a real image")
        _write_png(tmp_path / "good.png")

        published, fake_publish = _recording_publish(key=lambda msg: msg.payload["image_filename"])

        _run_main(
            monkeypatch,
            assets=_assets({"line-1": {"cam": {}}}),
            app_configuration={"camera": {"images_dir": str(tmp_path), "publish_interval": 1}},
            publish=fake_publish,
            max_cycles=2,
        )

        # The corrupt frame never publishes; the loop keeps going and delivers the good one.
        assert "bad.png" not in published
        assert "good.png" in published
        assert "Failed to encode image" in capsys.readouterr().out


async def _publish_raises_runtime(_msg) -> bool:
    raise RuntimeError("not connected")  # not connected -> loop breaks out of this cycle


async def _publish_returns_false(_msg) -> bool:
    return False  # SDK returns False on ConnectionError -> loop continues to next target


class TestPublishFailureModes:
    """Two failure modes with opposite loop control, contrasted via one parametrization.

    RuntimeError (connection down) -> `break`: remaining targets are skipped this cycle,
    so only the first of two mapped streams is ever attempted (1 call). A False result
    (not delivered) -> `continue`: every target is still attempted (2 calls). Both configs
    map two streams so the call count alone distinguishes break from continue.
    """

    @pytest.mark.parametrize(
        ("publish_fn", "expected_calls", "log_substring"),
        [
            pytest.param(_publish_raises_runtime, 1, "Publish failed", id="runtime-error-break"),
            pytest.param(_publish_returns_false, 2, "Publish not delivered", id="not-delivered-continue"),
        ],
    )
    def test_failure_mode_controls_remaining_targets(
        self,
        publish_fn,
        expected_calls: int,
        log_substring: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_png(tmp_path / "1.png")
        calls = 0

        async def counting_publish(msg) -> bool:
            nonlocal calls
            calls += 1
            return await publish_fn(msg)

        _run_main(
            monkeypatch,
            # Two streams -> two targets; break stops after the first, continue attempts both.
            assets=_assets({"line-1": {"cam-a": {}, "cam-b": {}}}),
            app_configuration={"camera": {"images_dir": str(tmp_path), "publish_interval": 1}},
            publish=counting_publish,
        )

        assert calls == expected_calls
        assert log_substring in capsys.readouterr().out


class TestCursorWraparound:
    def test_cursor_wraps_and_republishes_first_image(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        images = ["1.png", "2.png"]
        for name in images:
            _write_png(tmp_path / name)

        published, fake_publish = _recording_publish(key=lambda msg: msg.payload["image_filename"])

        # 3 cycles over 2 images forces the stored cursor to wrap 1 -> 0.
        _run_main(
            monkeypatch,
            assets=_assets({"line-1": {"cam": {}}}),
            app_configuration={"camera": {"images_dir": str(tmp_path), "publish_interval": 1}},
            publish=fake_publish,
            max_cycles=3,
        )

        # After exhausting the folder, playback restarts from the first image.
        assert published == ["1.png", "2.png", "1.png"]

    def test_single_file_republishes_every_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Degenerate len == 1 case: the cursor expression (0 + 1) % 1 == 0 keeps the sole
        # image selected every cycle, so it must republish on each pass.
        _write_png(tmp_path / "only.png")

        published, fake_publish = _recording_publish(key=lambda msg: msg.payload["image_filename"])

        _run_main(
            monkeypatch,
            assets=_assets({"line-1": {"cam": {}}}),
            app_configuration={"camera": {"images_dir": str(tmp_path), "publish_interval": 1}},
            publish=fake_publish,
            max_cycles=2,
        )

        # The single image is republished on both cycles.
        assert published == ["only.png", "only.png"]


class TestPerStreamCursor:
    def test_streams_sharing_a_dir_each_get_the_full_sequence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        images = ["1.png", "2.png", "3.png"]
        for name in images:
            _write_png(tmp_path / name)

        published: list[tuple[str, str, str]] = []

        async def fake_connect() -> None:
            pass

        async def fake_publish(msg) -> bool:
            published.append((msg.resource.asset, msg.resource.data_stream, msg.payload["image_filename"]))
            return True

        cycles = 0

        async def fake_sleep(_seconds) -> None:
            nonlocal cycles
            cycles += 1
            if cycles >= len(images):
                raise _StopLoop

        fake_app = SimpleNamespace(
            connect=fake_connect,
            publish=fake_publish,
            assets=_assets({"line-1": {"cam-a": {}, "cam-b": {}}}),
            app_configuration={"camera": {"images_dir": str(tmp_path), "publish_interval": 1}},
        )
        monkeypatch.setattr(main, "app", fake_app)
        monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

        with pytest.raises(_StopLoop):
            asyncio.run(main.main())

        # Both streams share tmp_path, yet each sees the whole sequence in order.
        for stream in ("cam-a", "cam-b"):
            frames = [f for asset, s, f in published if s == stream]
            assert frames == images
