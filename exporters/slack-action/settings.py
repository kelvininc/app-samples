import re

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")


class Slack(BaseModel):
    # SecretStr so a stray log/repr of the config masks the bot token instead of leaking it.
    token: SecretStr

    @field_validator("token", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # so a deployment that forgot to wire the secret fails fast (token is required).
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    slack: Slack
