import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator, model_validator
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
    # str_strip_whitespace so a blank/whitespace host fails its min_length check (a whitespace
    # from_address is rejected by the address validation instead).
    model_config = ConfigDict(str_strip_whitespace=True)

    host: str = Field(min_length=1)
    port: int = Field(default=587, ge=1, le=65535)
    use_tls: bool = True                            # STARTTLS (the common port-587 case)
    # Validated like the recipients in the action payload, so a typo'd sender is caught at connect
    # rather than by the relay. A display name ("Plant Alerts <alerts@x.com>") is accepted but
    # normalized away to the bare address; set the From display name on the relay if it matters.
    from_address: EmailStr
    auth: SmtpAuth = Field(default_factory=SmtpAuth)

    @model_validator(mode="after")
    def _require_tls_for_password_auth(self) -> "Smtp":
        # SMTP AUTH sends the username/password base64-encoded, which is encoding, not encryption.
        # Without TLS those credentials cross the wire in the clear, so refuse the combination at
        # config time rather than leak them at send time.
        if self.auth.method == "username_password" and not self.use_tls:
            raise ValueError(
                "auth.method='username_password' requires use_tls=true: password auth would send "
                "credentials over an unencrypted connection. Enable TLS to use password auth."
            )
        return self


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    smtp: Smtp
