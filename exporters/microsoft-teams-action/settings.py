import re

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")


class Teams(BaseModel):
    # The webhook URL embeds a token, so it's a SecretStr; a stray log/repr masks it.
    webhook_url: SecretStr

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # so a deployment that forgot to wire the secret fails fast (webhook_url is required).
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @field_validator("webhook_url")
    @classmethod
    def _reject_blank(cls, v: SecretStr) -> SecretStr:
        # A blank (or whitespace-only) URL would only fail later, on the first send;
        # fail configuration validation instead so the app never starts half-configured.
        if not v.get_secret_value().strip():
            raise ValueError("webhook_url must not be blank")
        return v


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    teams: Teams
