import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
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
    retry: Retry = Field(default_factory=Retry)     # default_factory: no shared mutable default


class Buffer(BaseModel):
    max_backlog: int = Field(default=0, ge=0)       # max un-uploaded rows kept before dropping oldest; 0 = unbounded


# --- provider block (per-app: how to reach Databricks Zerobus + how to authenticate) ---

class DatabricksAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks credentials instead of leaking them.
    # Zerobus authenticates only with an OAuth service principal (client_id + client_secret);
    # there is no access-token path.
    client_id: Optional[SecretStr] = None
    client_secret: Optional[SecretStr] = None

    @field_validator("client_id", "client_secret", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _require_both(self) -> "DatabricksAuth":
        if not (self.client_id and self.client_secret):
            raise ValueError("auth requires client_id and client_secret")
        return self


class Databricks(BaseModel):
    # str_strip_whitespace so a blank/whitespace host or endpoint fails the min_length check below.
    model_config = ConfigDict(str_strip_whitespace=True)

    server_hostname: str = Field(min_length=1)      # workspace hostname, used for OAuth
    zerobus_endpoint: str = Field(min_length=1)     # gRPC ingestion endpoint
    delta_table: str
    auth: DatabricksAuth

    @field_validator("delta_table")
    @classmethod
    def _valid_table(cls, v: str) -> str:
        if not _TABLE_RE.match(v):
            raise ValueError("delta_table must be 'catalog.schema.table' (alnum/underscore only)")
        return v


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    databricks: Databricks
    upload: Upload = Field(default_factory=Upload)
    buffer: Buffer = Field(default_factory=Buffer)
