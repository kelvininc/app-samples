"""Shared runtime manifest for the testing guides."""

from pathlib import Path

import pytest
import yaml
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import ManifestBuilder

PROJECT_MANIFEST = Path(__file__).resolve().parents[1] / "app.yaml"
DEFAULT_PARAMETERS = yaml.safe_load(PROJECT_MANIFEST.read_text())["defaults"][
    "parameters"
]


@pytest.fixture
def motor_manifest() -> RuntimeManifest:
    """Load declared I/O and supply one motor with the App's default parameters."""
    return (
        ManifestBuilder.from_app_yaml(PROJECT_MANIFEST)
        .add_asset("motor-01", parameters=DEFAULT_PARAMETERS)
        .build()
    )
