"""App settings: the MQTT connection/publish section plus the shared simulation models.

Only this module differs across the simulator apps; models.py, fleet.py and
waveforms.py are shared copies. The connection block mirrors the MQTT importer.
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


class MqttAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks the password instead of leaking it.
    # Both empty -> anonymous connection. Mirrors the MQTT importer's auth block.
    username: Optional[str] = None
    password: Optional[SecretStr] = None

    @field_validator("password", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _both_or_neither(self) -> "MqttAuth":
        if bool(self.username) != bool(self.password):
            raise ValueError("provide both username and password, or neither (anonymous broker)")
        return self


class Publish(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # Topic template; {asset} and {tag} expand per published value. {tag} is
    # invalid with payload=json_bundle (one message carries all of an asset's tags).
    topic: str = Field(default="sim/{asset}/{tag}", min_length=1)
    # The model stays an int Literal, but the deploy form's select widget submits enum values as
    # strings ("2"), so a before-validator casts the numeric string back to int (see _coerce_qos).
    qos: Literal[0, 1, 2] = 0

    @field_validator("qos", mode="before")
    @classmethod
    def _coerce_qos(cls, v: object) -> object:
        # bool is an int subclass, so qos=True would otherwise pass as 1; reject it explicitly.
        if isinstance(v, bool):
            raise ValueError("qos must be 0, 1 or 2, not a boolean")
        # Select widgets submit the chosen enum value as a string ("2"); cast a numeric string to
        # int so the Literal accepts it. Anything else falls through and the Literal rejects
        # out-of-range or non-numeric values as a recoverable ValidationError.
        if isinstance(v, str) and v.strip().isdigit():
            return int(v)
        return v
    payload: Payload = "json"
    timestamp: Timestamp = "iso"

    @model_validator(mode="after")
    def _tag_placeholder_valid(self) -> "Publish":
        if self.payload == "json_bundle" and "tag" in _PLACEHOLDER_RE.findall(self.topic):
            raise ValueError("topic cannot use {tag} with payload=json_bundle (one message per asset, not per tag)")
        return self


class Mqtt(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    host: str = Field(default="mqtt-broker", min_length=1)
    port: int = Field(default=21883, ge=1, le=65535)
    client_id: str = "mqtt-simulator"
    use_tls: bool = False                       # TLS (default SSL context); pair with the broker's secure port
    auth: MqttAuth = Field(default_factory=MqttAuth)
    publish: Publish = Field(default_factory=Publish)


class Settings(KelvinAppConfig):
    """Simulator configuration.

    Resolution order (KelvinAppConfig): env vars (nested with '__', e.g.
    MQTT__AUTH__PASSWORD) -> platform-delivered config.yaml -> the bundled
    app.yaml `defaults.configuration`.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        extra="ignore",
    )

    mqtt: Mqtt = Field(default_factory=Mqtt)
    simulation: Simulation = Field(default_factory=Simulation)
    assets: list[AssetGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_assets(self) -> "Settings":
        validate_unique_assets(self.assets)
        return self
