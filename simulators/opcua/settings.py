"""App settings: the OPC-UA protocol section plus the shared simulation models.

Only this module differs across the simulator apps; models.py, fleet.py and
waveforms.py are shared copies.
"""

import re

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from kelvin.config.appconfig import KelvinAppConfig

from models import AssetGroup, Simulation, validate_unique_assets

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")


class OpcuaAuth(BaseModel):
    """Username/password authentication. Both empty (the default) allows anonymous access."""

    username: str = ""
    # SecretStr so a stray log/repr of the config (including pydantic validation
    # errors) masks the credential instead of leaking it.
    password: SecretStr = SecretStr("")

    @field_validator("username", "password", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # so a deployment without the secret falls back to anonymous instead of requiring
        # the placeholder string as a credential.
        return "" if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @property
    def enabled(self) -> bool:
        return bool(self.username and self.password.get_secret_value())


class Opcua(BaseModel):
    port: int = Field(default=50000, ge=1, le=65535)
    # Hostname advertised in GetEndpoints; clients reconnect to it. Empty ->
    # KELVIN_WORKLOAD_NAME (the workload's service DNS name), then localhost.
    advertised_host: str = ""
    auth: OpcuaAuth = Field(default_factory=OpcuaAuth)


class Settings(KelvinAppConfig):
    """Simulator configuration.

    Resolution order (KelvinAppConfig): env vars (nested with '__', e.g.
    OPCUA__AUTH__PASSWORD) -> platform-delivered config.yaml -> the bundled
    app.yaml `defaults.configuration` (found last on the YAML search path,
    relative to the /opt/kelvin/app workdir).
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        extra="ignore",
    )

    opcua: Opcua = Field(default_factory=Opcua)
    simulation: Simulation = Field(default_factory=Simulation)
    assets: list[AssetGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_assets(self) -> "Settings":
        validate_unique_assets(self.assets)
        return self
