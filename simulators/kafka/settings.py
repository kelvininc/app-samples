"""App settings: the Kafka connection/publish section plus the shared simulation models.

Only this module differs across the simulator apps; models.py, fleet.py,
waveforms.py and messages.py are shared copies. The `security` block mirrors
the Kafka importer/exporter so one broker's config is copy-pasteable between them.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from kelvin.config.appconfig import KelvinAppConfig

from models import AssetGroup, Simulation, validate_unique_assets

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

Payload = Literal["raw", "json", "json_bundle"]
Timestamp = Literal["iso", "epoch_ms", "none"]


# The `security` dimension (protocol + sasl + tls) is shared with the Kafka connectors; keep the
# models in sync so one broker's config is copy-pasteable across the connectors and this simulator.

class KafkaSasl(BaseModel):
    # SecretStr so a stray log/repr of the config masks the password instead of leaking it.
    # All empty -> no SASL (PLAINTEXT/SSL); set all three together to authenticate.
    mechanism: Optional[Literal["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"]] = None
    username: Optional[str] = None
    password: Optional[SecretStr] = None

    @field_validator("password", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _all_or_nothing(self) -> "KafkaSasl":
        provided = [bool(self.mechanism), bool(self.username), bool(self.password)]
        if any(provided) and not all(provided):
            raise ValueError("SASL requires mechanism, username and password together (or none)")
        return self


class KafkaTls(BaseModel):
    # PEM *content*, not paths: config values arrive through the platform (wired to secrets).
    # ca_cert set -> it REPLACES the system trust store (private CA);
    # client_cert+client_key together enable mTLS.
    ca_cert: str = ""
    client_cert: str = ""
    client_key: Optional[SecretStr] = None

    @field_validator("ca_cert", "client_cert", mode="before")
    @classmethod
    def _reject_unresolved_secret_str(cls, v: object) -> object:
        return "" if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @field_validator("client_key", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _cert_and_key_together(self) -> "KafkaTls":
        if bool(self.client_cert) != bool(self.client_key):
            raise ValueError("mTLS requires client_cert and client_key together (or neither)")
        return self

    def configured(self) -> bool:
        """Whether any TLS material is set (drives the protocol-coherence check)."""
        return bool(self.ca_cert or self.client_cert or self.client_key)


class KafkaSecurity(BaseModel):
    protocol: Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"] = "PLAINTEXT"
    sasl: KafkaSasl = Field(default_factory=KafkaSasl)
    tls: KafkaTls = Field(default_factory=KafkaTls)

    @model_validator(mode="after")
    def _blocks_match_protocol(self) -> "KafkaSecurity":
        is_sasl = self.protocol.startswith("SASL")
        if is_sasl and not self.sasl.mechanism:
            raise ValueError(f"protocol {self.protocol} requires a sasl block")
        if not is_sasl and self.sasl.mechanism:
            raise ValueError(f"sasl is set but protocol {self.protocol} is not SASL_*")
        if self.tls.configured() and self.protocol not in ("SSL", "SASL_SSL"):
            raise ValueError(f"tls is set but protocol {self.protocol} is not SSL/SASL_SSL")
        return self


class Publish(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # Topic/key templates; {asset} and {tag} expand per record. {tag} is invalid
    # with payload=json_bundle (one record carries all of an asset's tags).
    topic: str = Field(default="sim.{asset}", min_length=1)
    key: str = Field(default="{tag}", min_length=1)
    payload: Payload = "json"
    timestamp: Timestamp = "iso"

    @model_validator(mode="after")
    def _tag_placeholder_valid(self) -> "Publish":
        if self.payload == "json_bundle":
            for field in ("topic", "key"):
                if "tag" in _PLACEHOLDER_RE.findall(getattr(self, field)):
                    raise ValueError(f"{field} cannot use {{tag}} with payload=json_bundle (one record per asset)")
        return self


class Kafka(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    bootstrap_servers: str = Field(default="my-kafka:9092", min_length=1)
    security: KafkaSecurity = Field(default_factory=KafkaSecurity)
    publish: Publish = Field(default_factory=Publish)


class Settings(KelvinAppConfig):
    """Simulator configuration.

    Resolution order (KelvinAppConfig): env vars (nested with '__', e.g.
    KAFKA__SECURITY__SASL__PASSWORD) -> platform-delivered config.yaml -> the
    bundled app.yaml `defaults.configuration`.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        extra="ignore",
    )

    kafka: Kafka = Field(default_factory=Kafka)
    simulation: Simulation = Field(default_factory=Simulation)
    assets: list[AssetGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_assets(self) -> "Settings":
        validate_unique_assets(self.assets)
        return self
