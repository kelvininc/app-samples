import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")


# --- shared sub-models (Retry/Buffer identical across the DuckDB exporters; Upload varies per sink) ---

class Retry(BaseModel):
    # keyword default= (not positional) so the type checker sees these as optional fields.
    attempts: int = Field(default=3, ge=1)
    base_delay: float = Field(default=1.0, ge=0)
    max_delay: float = Field(default=30.0, ge=0)    # backoff ceiling, independent of upload.interval


class Upload(BaseModel):
    interval: int = Field(default=60, ge=1)         # ge=1: a 0s interval busy-spins the drain loop
    batch_size: int = Field(default=1000, ge=1)
    format: Literal["parquet", "csv", "json"] = "parquet"   # file sink: DuckDB COPY output format
    retry: Retry = Field(default_factory=Retry)     # default_factory: no shared mutable default


class Buffer(BaseModel):
    max_backlog: int = Field(default=0, ge=0)       # max un-uploaded rows kept before dropping oldest; 0 = unbounded


# --- provider block (per-app: how to reach the SFTP server + how to authenticate) ---

class SftpAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks credentials instead of leaking them.
    method: Literal["password", "private_key"] = "password"
    password: Optional[SecretStr] = None
    private_key: Optional[SecretStr] = None             # PEM-encoded private key (RSA/Ed25519/ECDSA)
    private_key_passphrase: Optional[SecretStr] = None  # optional, if the key is encrypted

    @field_validator("password", "private_key", "private_key_passphrase", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _one_auth(self) -> "SftpAuth":
        if self.method == "password" and not self.password:
            raise ValueError("auth.method='password' requires password")
        if self.method == "private_key" and not self.private_key:
            raise ValueError("auth.method='private_key' requires private_key")
        return self


class Sftp(BaseModel):
    # str_strip_whitespace so a blank/whitespace host or username fails the min_length checks.
    model_config = ConfigDict(str_strip_whitespace=True)

    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    remote_dir: str = "."                           # remote directory to upload batches into
    timeout: float = Field(default=30, gt=0)        # network timeout (s) for connect/auth/transfers
    known_hosts: Optional[str] = None               # path to a known_hosts file (host-key verification)
    verify_host_key: bool = True                    # False auto-accepts unknown keys (dev only; MITM risk)
    auth: SftpAuth


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    sftp: Sftp
    upload: Upload = Field(default_factory=Upload)
    buffer: Buffer = Field(default_factory=Buffer)
