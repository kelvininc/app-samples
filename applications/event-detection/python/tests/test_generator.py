"""Verify the manual generator's import and finite message sequence."""

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_generator_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load the CLI entrypoint and collect its values without real-time waits."""
    path = Path(__file__).resolve().parents[1] / "temperature-generator.py"
    spec = importlib.util.spec_from_file_location("temperature_generator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def no_wait(delay: float) -> None:
        """Replace only the generator's wall-clock wait during this sequence test."""
        assert delay == 1

    monkeypatch.setattr(module.asyncio, "sleep", no_wait)
    messages = [message async for message in module.MotorTemperatureGenerator().run()]
    assert [message.payload for message in messages] == [45.0, 80.0, 81.0]
    assert all(message.resource.asset == "test-asset-1" for message in messages)
    assert all(message.resource.data_stream == "temperature_c" for message in messages)
