import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")


class SmtpAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks the password instead of leaking it.
    # method="none" -> unauthenticated send (e.g. an internal relay that trusts the network);
    # method="username_password" -> both credentials are required.
    method: Literal["none", "username_password"] = "none"
    username: Optional[str] = None
    password: Optional[SecretStr] = None

    @field_validator("username", "password", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # so an un-wired credential fails the method check below instead of passing the literal.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _one_auth(self) -> "SmtpAuth":
        if self.method == "username_password" and not (self.username and self.password):
            raise ValueError("auth.method='username_password' requires username and password")
        return self


class Smtp(BaseModel):
    # str_strip_whitespace so a blank/whitespace host or from_address fails the min_length checks.
    model_config = ConfigDict(str_strip_whitespace=True)

    host: str = Field(min_length=1)
    port: int = Field(default=587, ge=1, le=65535)
    use_tls: bool = True                            # STARTTLS (the common port-587 case)
    from_address: str = Field(min_length=1)
    auth: SmtpAuth = Field(default_factory=SmtpAuth)


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    smtp: Smtp
