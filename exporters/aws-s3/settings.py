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
    max_backlog: int = Field(default=1_000_000, ge=0)       # max un-uploaded rows kept before dropping oldest; 0 = unbounded (explicit opt-in)


# --- provider block (per-app: how to reach S3 + how to authenticate) ---

class S3Auth(BaseModel):
    # SecretStr so a stray log/repr of the config masks credentials instead of leaking them.
    # Both empty -> boto3 falls back to the AWS default credential chain (IAM role, env, ...).
    access_key_id: Optional[SecretStr] = None
    secret_access_key: Optional[SecretStr] = None

    @field_validator("access_key_id", "secret_access_key", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _both_or_neither(self) -> "S3Auth":
        if bool(self.access_key_id) != bool(self.secret_access_key):
            raise ValueError("provide both access_key_id and secret_access_key, or neither (default chain)")
        return self


class S3(BaseModel):
    # str_strip_whitespace so a blank/whitespace region or bucket fails the min_length check below.
    model_config = ConfigDict(str_strip_whitespace=True)

    region: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    prefix: str = ""                            # optional key prefix ("folder") within the bucket
    auth: S3Auth = Field(default_factory=S3Auth)


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    s3: S3
    upload: Upload = Field(default_factory=Upload)
    buffer: Buffer = Field(default_factory=Buffer)
