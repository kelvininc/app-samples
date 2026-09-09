"""Tests for load_settings: the configuration gate that runs before the simulator starts."""
import pytest

import main
from settings import Settings


def test_bundled_defaults_define_a_fleet() -> None:
    """Control for the test below: the shipped app.yaml defaults load and define assets."""
    settings = main.load_settings()
    assert settings.assets


def test_empty_fleet_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly empty fleet simulates nothing, so the app exits instead of idling.

    The ui_schema also rejects an empty `assets` (required + minItems), but a config can reach
    the app without passing through the form, so this stays the gate.
    """
    monkeypatch.setattr(main, "Settings", lambda: Settings(assets=[]))
    with pytest.raises(SystemExit) as excinfo:
        main.load_settings()
    assert excinfo.value.code == 1


def test_invalid_configuration_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configuration that fails validation exits rather than propagating a traceback."""
    monkeypatch.setenv("SIMULATION__TICK", "0")          # tick must be > 0
    with pytest.raises(SystemExit) as excinfo:
        main.load_settings()
    assert excinfo.value.code == 1
