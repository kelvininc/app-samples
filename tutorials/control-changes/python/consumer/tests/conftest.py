"""Runtime context for the transferred receiver harness exercise."""

import pytest
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import ManifestBuilder


@pytest.fixture
def motor_manifest() -> RuntimeManifest:
    """Load the consumer declaration and assign one motor."""
    return (
        ManifestBuilder.from_app_yaml()
        .add_custom_action_input("run_diagnostics")
        .add_asset("motor-01")
        .build()
    )
