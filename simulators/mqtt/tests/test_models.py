"""Unit tests for the shared configuration models and validation rules."""

import pytest
from pydantic import ValidationError

from models import AssetGroup, TagSpec, validate_unique_assets


# ====================================================
# Test Cases: TagSpec validation
# ====================================================

def test_tag_defaults() -> None:
    """Test that a bare TagSpec gets the documented defaults."""
    spec = TagSpec()
    assert spec.waveform == "constant"
    assert spec.type == "float"
    assert spec.min == 0.0
    assert spec.max == 100.0
    assert spec.writable is False


def test_max_must_exceed_min() -> None:
    """Test that an inverted range is rejected."""
    with pytest.raises(ValidationError, match="max .* must be greater than min"):
        TagSpec(min=10, max=10)


def test_writable_tag_rejects_waveform() -> None:
    """Test that writable and waveform are mutually exclusive."""
    with pytest.raises(ValidationError, match="writable tags cannot have a waveform"):
        TagSpec(writable=True, waveform="sine")


def test_bool_tag_rejects_continuous_waveforms() -> None:
    """Test that bool tags only accept square/random/constant waveforms."""
    with pytest.raises(ValidationError, match="bool tags support"):
        TagSpec(type="bool", waveform="sine")


def test_writable_constant_is_valid() -> None:
    """Test that the standard setpoint shape (writable + initial) validates."""
    spec = TagSpec(writable=True, initial=8.0)
    assert spec.writable is True
    assert spec.initial == 8.0


# ====================================================
# Test Cases: AssetGroup validation
# ====================================================

def test_asset_requires_at_least_one_tag() -> None:
    """Test that an asset with an empty tag map is rejected."""
    with pytest.raises(ValidationError):
        AssetGroup(name="BeamPump", tags={})


@pytest.mark.parametrize("bad_name", ["has space", "semi;colon", ""])
def test_asset_rejects_invalid_tag_names(bad_name: str) -> None:
    """Test that tag names outside [A-Za-z0-9_.-] are rejected (they become point ids)."""
    with pytest.raises(ValidationError):
        AssetGroup(name="BeamPump", tags={bad_name: TagSpec()})


@pytest.mark.parametrize("bad_name", ["Beam Pump", "pump/01", ""])
def test_asset_rejects_invalid_names(bad_name: str) -> None:
    """Test that asset names outside [A-Za-z0-9_.-] are rejected (they become identifiers)."""
    with pytest.raises(ValidationError):
        AssetGroup(name=bad_name, tags={"spm": TagSpec()})


# ====================================================
# Test Cases: unique asset names across groups
# ====================================================

def test_duplicate_assets_rejected() -> None:
    """Test that two groups with the same name are rejected with a clear message.

    Edge case: point ids are lowercased on some protocols, so 'BeamPump' and
    'beampump' collide; the check must be case-insensitive.
    """
    assets = [
        AssetGroup(name="BeamPump", tags={"spm": TagSpec()}),
        AssetGroup(name="beampump", tags={"spm": TagSpec()}),
    ]
    with pytest.raises(ValueError, match="duplicate asset name 'beampump'"):
        validate_unique_assets(assets)


def test_unique_assets_pass_through() -> None:
    """Test that distinct asset names validate unchanged."""
    assets = [
        AssetGroup(name="BeamPump", tags={"spm": TagSpec()}),
        AssetGroup(name="PCP", tags={"torque": TagSpec()}),
    ]
    assert validate_unique_assets(assets) == assets
