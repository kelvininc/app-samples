"""Unit tests for the app settings: auth handling and cross-field validation."""

import pytest
from pydantic import SecretStr, ValidationError

from models import AssetGroup, TagSpec
from settings import OpcuaAuth, Settings


# ====================================================
# Test Cases: auth secrets handling
# ====================================================

def test_auth_disabled_when_empty() -> None:
    """Test that empty credentials mean anonymous access."""
    assert OpcuaAuth().enabled is False


def test_auth_enabled_with_credentials() -> None:
    """Test that a full username/password pair enables authentication."""
    assert OpcuaAuth(username="operator", password=SecretStr("secret")).enabled is True


def test_password_is_masked_in_repr() -> None:
    """Test that the password never appears in the model's repr (log safety)."""
    auth = OpcuaAuth(username="operator", password=SecretStr("hunter2"))
    assert "hunter2" not in repr(auth)


def test_unresolved_secret_placeholder_treated_as_unset() -> None:
    """Test that a literal '<% secrets.x %>' placeholder does not become a credential.

    Edge case: deploying without creating the secret leaves the template string
    in place; auth must fall back to anonymous, not require the placeholder.
    """
    auth = OpcuaAuth(username="<% secrets.opc-sim-user %>", password="<% secrets.opc-sim-password %>")
    assert auth.enabled is False


# ====================================================
# Test Cases: Settings-level validation
# ====================================================

def test_settings_reject_duplicate_assets() -> None:
    """Test that duplicate asset names fail Settings validation with a clear error."""
    assets = [
        AssetGroup(name="BeamPump", tags={"spm": TagSpec()}),
        AssetGroup(name="BeamPump", tags={"spm": TagSpec()}),
    ]
    with pytest.raises(ValidationError, match="duplicate asset name"):
        Settings(assets=assets)
