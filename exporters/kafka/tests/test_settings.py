"""Unit tests for the nested Settings model (Kafka: security dimension = protocol + sasl + tls)."""
import pytest
from pydantic import ValidationError

from settings import Settings

SASL = {"mechanism": "PLAIN", "username": "u", "password": "p"}
KAFKA = {"bootstrap_servers": "broker:9093"}


def test_minimal_config_defaults() -> None:
    """Brokers alone are enough: plaintext, no credentials, exporter client id."""
    s = Settings(kafka=KAFKA)
    assert s.kafka.bootstrap_servers == "broker:9093"
    assert s.kafka.client_id == "kelvin-kafka-exporter"
    assert s.kafka.security.protocol == "PLAINTEXT"
    assert s.kafka.security.sasl.mechanism is None
    assert not s.kafka.security.tls.configured()


def test_requires_kafka_block() -> None:
    """No kafka block (or no brokers) fails fast at config time, like every sibling exporter."""
    with pytest.raises(ValidationError):
        Settings()
    with pytest.raises(ValidationError):
        Settings(kafka={"client_id": "exp-1"})


def test_override() -> None:
    s = Settings(kafka={**KAFKA, "client_id": "exp-1", "security": {"protocol": "SSL"}})
    assert s.kafka.bootstrap_servers == "broker:9093" and s.kafka.client_id == "exp-1"
    assert s.kafka.security.protocol == "SSL"


class TestSasl:
    """SASL credential validation inside the security block."""

    def test_full_sasl_and_masks_password(self) -> None:
        s = Settings(kafka={**KAFKA, "security": {"protocol": "SASL_SSL",
                                         "sasl": {**SASL, "mechanism": "SCRAM-SHA-256", "password": "shhh"}}})
        assert s.kafka.security.sasl.password.get_secret_value() == "shhh"
        assert "shhh" not in repr(s.kafka.security.sasl)

    @pytest.mark.parametrize("partial", [
        {"mechanism": "PLAIN"}, {"username": "u"}, {"mechanism": "PLAIN", "username": "u"},
    ])
    def test_rejects_incomplete_sasl(self, partial: dict) -> None:
        with pytest.raises(ValidationError, match="mechanism, username and password"):
            Settings(kafka={**KAFKA, "security": {"sasl": partial}})

    def test_rejects_unknown_mechanism(self) -> None:
        with pytest.raises(ValidationError):
            Settings(kafka={**KAFKA, "security": {"sasl": {**SASL, "mechanism": "KERBEROS"}}})

    def test_unresolved_secret_password_treated_as_unset(self) -> None:
        """A never-resolved '<% secrets.x %>' literal normalizes to None and fails all-or-nothing."""
        with pytest.raises(ValidationError, match="mechanism, username and password"):
            Settings(kafka={**KAFKA, "security": {"sasl": {**SASL, "password": "<% secrets.kafka-password %>"}}})


class TestProtocolCoherence:
    """protocol and the sasl/tls blocks must agree (mismatches fail at config time)."""

    def test_rejects_unknown_protocol(self) -> None:
        with pytest.raises(ValidationError):
            Settings(kafka={**KAFKA, "security": {"protocol": "WHATEVER"}})

    @pytest.mark.parametrize("protocol", ["SASL_SSL", "SASL_PLAINTEXT"])
    def test_sasl_protocol_without_credentials_rejected(self, protocol: str) -> None:
        with pytest.raises(ValidationError, match="requires a sasl block"):
            Settings(kafka={**KAFKA, "security": {"protocol": protocol}})

    @pytest.mark.parametrize("protocol", ["PLAINTEXT", "SSL"])
    def test_non_sasl_protocol_with_credentials_rejected(self, protocol: str) -> None:
        with pytest.raises(ValidationError, match="not SASL"):
            Settings(kafka={**KAFKA, "security": {"protocol": protocol, "sasl": SASL}})

    def test_matched_sasl_config_accepted(self) -> None:
        s = Settings(kafka={**KAFKA, "security": {"protocol": "SASL_SSL", "sasl": SASL}})
        assert s.kafka.security.protocol == "SASL_SSL" and s.kafka.security.sasl.mechanism == "PLAIN"


class TestTls:
    """TLS material: PEM content, mTLS pairing, protocol coherence."""

    def test_ca_cert_with_ssl_accepted(self) -> None:
        s = Settings(kafka={**KAFKA, "security": {"protocol": "SSL", "tls": {"ca_cert": "PEM"}}})
        assert s.kafka.security.tls.ca_cert == "PEM" and s.kafka.security.tls.configured()

    def test_mtls_pair_with_sasl_ssl_accepted(self) -> None:
        s = Settings(kafka={**KAFKA, "security": {"protocol": "SASL_SSL", "sasl": SASL,
                                         "tls": {"client_cert": "CERT", "client_key": "KEY"}}})
        assert s.kafka.security.tls.client_key.get_secret_value() == "KEY"

    @pytest.mark.parametrize("partial", [{"client_cert": "CERT"}, {"client_key": "KEY"}])
    def test_rejects_cert_without_key_and_vice_versa(self, partial: dict) -> None:
        with pytest.raises(ValidationError, match="client_cert and client_key together"):
            Settings(kafka={**KAFKA, "security": {"protocol": "SSL", "tls": partial}})

    @pytest.mark.parametrize("protocol", ["PLAINTEXT", "SASL_PLAINTEXT"])
    def test_tls_material_under_plaintext_rejected(self, protocol: str) -> None:
        security = {"protocol": protocol, "tls": {"ca_cert": "PEM"}}
        if protocol.startswith("SASL"):
            security["sasl"] = SASL
        with pytest.raises(ValidationError, match="not SSL/SASL_SSL"):
            Settings(kafka={**KAFKA, "security": security})

    def test_client_key_is_masked_in_repr(self) -> None:
        """The private key must never leak through a repr/log of the config."""
        key = "-----BEGIN PRIVATE KEY-----secret"
        tls = Settings(kafka={**KAFKA, "security": {"protocol": "SSL",
                                           "tls": {"client_cert": "CERT", "client_key": key}}}).kafka.security.tls
        assert key not in repr(tls)
        assert tls.client_key.get_secret_value() == key

    def test_unresolved_secret_tls_material_treated_as_unset(self) -> None:
        """Unresolved '<% secrets.x %>' placeholders normalize to unset, keeping tls unconfigured."""
        s = Settings(kafka={**KAFKA, "security": {"protocol": "PLAINTEXT",
                                         "tls": {"ca_cert": "<% secrets.kafka-ca-cert %>"}}})
        assert not s.kafka.security.tls.configured()    # placeholder never counts as TLS material


@pytest.mark.parametrize("blank", ["", "  "])
def test_rejects_blank_bootstrap(blank: str) -> None:
    with pytest.raises(ValidationError):
        Settings(kafka={"bootstrap_servers": blank})


class TestUploadAndBuffer:
    """Upload/buffer knobs: coercion, bounds, defaults."""

    def test_coerces_numeric_strings(self) -> None:
        s = Settings(kafka=KAFKA, upload={"batch_size": "500", "interval": "10"})
        assert s.upload.batch_size == 500 and s.upload.interval == 10

    @pytest.mark.parametrize("bad", [{"batch_size": 0}, {"batch_size": -1}, {"interval": 0}, {"interval": -1}])
    def test_rejects_out_of_bounds(self, bad: dict) -> None:
        """Upload knobs enforce their ge= bounds (interval ge=1: 0 would busy-spin the drain)."""
        with pytest.raises(ValidationError):
            Settings(kafka=KAFKA, upload=bad)

    def test_defaults(self) -> None:
        s = Settings(kafka=KAFKA)
        assert s.upload.batch_size == 1000 and s.upload.interval == 60
        assert s.upload.retry.attempts == 3 and s.upload.retry.max_delay == 30.0
        assert s.buffer.max_backlog == 1_000_000     # bounded by default; 0 = unbounded opt-in


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = Settings(kafka=KAFKA, some_platform_key="x")
    assert s.upload.batch_size == 1000
