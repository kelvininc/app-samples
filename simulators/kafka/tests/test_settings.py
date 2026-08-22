"""Unit tests for the Kafka app settings: security, publish validation, cross-field rules."""

import pytest
from pydantic import SecretStr, ValidationError

from models import AssetGroup, TagSpec
from settings import KafkaSecurity, Publish, Settings


# ====================================================
# Test Cases: security block coherence
# ====================================================

def test_plaintext_by_default() -> None:
    """Test that the default security is PLAINTEXT with no sasl/tls."""
    sec = KafkaSecurity()
    assert sec.protocol == "PLAINTEXT"


def test_sasl_protocol_requires_sasl_block() -> None:
    """Test that a SASL_* protocol without a mechanism is rejected."""
    with pytest.raises(ValidationError, match="requires a sasl block"):
        KafkaSecurity(protocol="SASL_PLAINTEXT")


def test_sasl_block_requires_matching_protocol() -> None:
    """Test that a sasl block with a PLAINTEXT protocol is rejected."""
    with pytest.raises(ValidationError, match="not SASL"):
        KafkaSecurity(
            protocol="PLAINTEXT",
            sasl={"mechanism": "PLAIN", "username": "u", "password": SecretStr("p")},
        )


def test_tls_requires_ssl_protocol() -> None:
    """Test that TLS material with a PLAINTEXT protocol is rejected."""
    with pytest.raises(ValidationError, match="not SSL"):
        KafkaSecurity(protocol="PLAINTEXT", tls={"ca_cert": "-----BEGIN CERTIFICATE-----"})


def test_sasl_all_or_nothing() -> None:
    """Test that a partial SASL block (mechanism only) is rejected."""
    with pytest.raises(ValidationError, match="SASL requires mechanism, username and password"):
        KafkaSecurity(protocol="SASL_PLAINTEXT", sasl={"mechanism": "PLAIN"})


# ====================================================
# Test Cases: publish validation
# ====================================================

@pytest.mark.parametrize("field,value", [("topic", "sim.{asset}.{tag}"), ("key", "{tag}")])
def test_tag_placeholder_rejected_with_bundle(field: str, value: str) -> None:
    """Test that {tag} in the topic or key is rejected when payload=json_bundle."""
    kwargs = {"topic": "sim.{asset}", "key": "{asset}", "payload": "json_bundle", field: value}
    with pytest.raises(ValidationError, match=r"cannot use \{tag\} with payload=json_bundle"):
        Publish(**kwargs)


def test_bundle_with_asset_templates_is_valid() -> None:
    """Test that json_bundle validates with {asset}-only topic and key."""
    pub = Publish(topic="sim.{asset}", key="{asset}", payload="json_bundle")
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
