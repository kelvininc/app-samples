"""Unit tests for the camera connector Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings


def test_defaults() -> None:
    s = Settings()
    assert s.camera.images_dir == "images" and s.camera.publish_interval == 30


def test_override() -> None:
    s = Settings(camera={"images_dir": "/data/frames", "publish_interval": 5})
    assert s.camera.images_dir == "/data/frames" and s.camera.publish_interval == 5


@pytest.mark.parametrize("blank", ["", "   "])
def test_rejects_blank_images_dir(blank: str) -> None:
    with pytest.raises(ValidationError):
        Settings(camera={"images_dir": blank})


@pytest.mark.parametrize("bad", [0, -1, 100000])
def test_rejects_out_of_range_interval(bad: int) -> None:
    with pytest.raises(ValidationError):
        Settings(camera={"publish_interval": bad})


def test_ignores_unknown_top_level_keys() -> None:
    assert Settings(some_platform_key="x").camera.publish_interval == 30
