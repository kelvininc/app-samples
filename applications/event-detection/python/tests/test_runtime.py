"""Verify that the test manifest supplies initialized App context."""

import pytest
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest
from main import app


@pytest.mark.asyncio
async def test_runtime_context(motor_manifest: RuntimeManifest) -> None:
    """Load a motor assignment through the in-memory harness."""
    async with KelvinAppTest(app, manifest=motor_manifest) as harness:
        assert app.is_connected
        assert app.config_received.is_set()
        assert app.assets["motor-01"].parameters["temperature_max_threshold"] == 80
        assert harness.outputs == []
