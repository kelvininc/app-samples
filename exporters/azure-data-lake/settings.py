import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
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


# --- provider block (per-app: how to reach ADLS + how to authenticate) ---

class ADLSAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks the key instead of leaking it.
    account_key: Optional[SecretStr] = None

    @field_validator("account_key", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v


class ADLS(BaseModel):
    # str_strip_whitespace so a blank/whitespace account_name or container fails min_length below.
    model_config = ConfigDict(str_strip_whitespace=True)

    # Azure storage account name shape (3-24 lowercase alphanumerics): the value is
    # interpolated into the account URL, so an arbitrary string could redirect requests.
    account_name: str = Field(min_length=3, max_length=24, pattern=r"^[a-z0-9]+$")
    container: str = Field(min_length=1)
    auth: ADLSAuth = Field(default_factory=ADLSAuth)


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    adls: ADLS
    upload: Upload = Field(default_factory=Upload)
    buffer: Buffer = Field(default_factory=Buffer)
