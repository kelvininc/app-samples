"""Unit tests for the MQTT app settings: auth, publish validation, cross-field rules."""

import pytest
from pydantic import SecretStr, ValidationError

from models import AssetGroup, TagSpec
from settings import MqttAuth, Publish, Settings


# ====================================================
# Test Cases: auth
# ====================================================

def test_auth_anonymous_by_default() -> None:
    """Test that empty auth is valid (anonymous broker)."""
    assert MqttAuth().username is None


def test_auth_requires_both_or_neither() -> None:
    """Test that a username without a password (or vice versa) is rejected."""
    with pytest.raises(ValidationError, match="both username and password"):
        MqttAuth(username="operator")


def test_unresolved_secret_placeholder_treated_as_unset() -> None:
    """Test that an unconfigured '<% secrets.x %>' password normalizes to unset.

    Edge case: with no username either, this must stay a valid anonymous config.
    """
    auth = MqttAuth(password="<% secrets.mqtt-sim-password %>")
    assert auth.password is None


def test_password_is_masked_in_repr() -> None:
    """Test that the password never appears in the model's repr (log safety)."""
    auth = MqttAuth(username="operator", password=SecretStr("hunter2"))
    assert "hunter2" not in repr(auth)


# ====================================================
# Test Cases: publish validation
# ====================================================

def test_tag_placeholder_rejected_with_bundle() -> None:
    """Test that {tag} in the topic is rejected when payload=json_bundle.

    A bundle carries all of an asset's tags, so there is no single {tag}.
    """
    with pytest.raises(ValidationError, match=r"cannot use \{tag\} with payload=json_bundle"):
        Publish(topic="sim/{asset}/{tag}", payload="json_bundle")


def test_bundle_with_asset_only_topic_is_valid() -> None:
    """Test that json_bundle validates with an {asset}-only topic."""
    pub = Publish(topic="sim/{asset}", payload="json_bundle")
    assert pub.payload == "json_bundle"


# ====================================================
# Test Cases: Settings-level validation
# ====================================================

def test_settings_reject_duplicate_assets() -> None:
    """Test that duplicate asset names fail Settings validation."""
    assets = [
        AssetGroup(name="BeamPump", tags={"spm": TagSpec()}),
        AssetGroup(name="BeamPump", tags={"spm": TagSpec()}),
    ]
    with pytest.raises(ValidationError, match="duplicate asset name"):
        Settings(assets=assets)


@pytest.mark.parametrize("submitted,expected", [("0", 0), ("1", 1), ("2", 2)])
def test_coerces_string_qos_to_int(submitted: str, expected: int) -> None:
    """The deploy form's select submits enum values as strings; coerce them to the int Literal."""
    s = Settings(mqtt={"publish": {"qos": submitted}})
    assert s.mqtt.publish.qos == expected and isinstance(s.mqtt.publish.qos, int)


def test_rejects_bool_qos() -> None:
    """bool is an int subclass, so qos=True would slip through as 1; it must be rejected."""
    with pytest.raises(ValidationError):
        Settings(mqtt={"publish": {"qos": True}})


@pytest.mark.parametrize("bad", ["3", "abc", -1])
def test_rejects_out_of_range_qos(bad: object) -> None:
    with pytest.raises(ValidationError):
        Settings(mqtt={"publish": {"qos": bad}})
