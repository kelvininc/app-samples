import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")


class MqttAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks the password instead of leaking it.
    # Both empty -> anonymous connection (e.g. a public/test broker).
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


class Mqtt(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    host: str = Field(default="test.mosquitto.org", min_length=1)
    port: int = Field(default=1883, ge=1, le=65535)
    client_id: str = "kelvin-mqtt-importer"
    use_tls: bool = False                       # TLS (default SSL context); pair with port 8883
    auth: MqttAuth = Field(default_factory=MqttAuth)


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    mqtt: Mqtt = Field(default_factory=Mqtt)
    reconnect_interval: int = Field(default=5, ge=1, le=300)
    # MQTT subscribe QoS for inbound topics: 0 at-most-once, 1 at-least-once, 2 exactly-once.
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
