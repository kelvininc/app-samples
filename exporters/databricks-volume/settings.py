import re
from typing import Literal, Optional

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
    # parquet/csv only: both carry the payload as scalar-JSON *text*, so the ingestion job's
    # parse_json(payload) round-trips uniformly. JSON export embeds payload as a native value
    # (mixed types across rows), which needs a separate VARIANT ingestion path; out of scope here.
    format: Literal["parquet", "csv"] = Field(default="parquet")
    retry: Retry = Field(default_factory=Retry)     # default_factory: no shared mutable default


class Buffer(BaseModel):
    max_backlog: int = Field(default=1_000_000, ge=0)       # max un-uploaded rows kept before dropping oldest; 0 = unbounded (explicit opt-in)


# --- provider block (per-app: how to reach Databricks + how to authenticate) ---

class DatabricksAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks credentials instead of leaking them.
    method: Literal["oauth", "access_token"] = "oauth"
    client_id: Optional[SecretStr] = None
    client_secret: Optional[SecretStr] = None
    access_token: Optional[SecretStr] = None

    @field_validator("access_token", "client_id", "client_secret", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _one_auth(self) -> "DatabricksAuth":
        if self.method == "access_token" and not self.access_token:
            raise ValueError("auth.method='access_token' requires access_token")
        if self.method == "oauth" and not (self.client_id and self.client_secret):
            raise ValueError("auth.method='oauth' requires client_id and client_secret")
        return self


class DatabricksJob(BaseModel):
    # Both optional: warehouse_id -> COPY INTO job; cluster_id -> Auto Loader job; neither -> no job.
    cluster_id: Optional[str] = None
    warehouse_id: Optional[str] = None


class Databricks(BaseModel):
    # str_strip_whitespace so a blank/whitespace host fails the min_length check below.
    model_config = ConfigDict(str_strip_whitespace=True)

    server_hostname: str = Field(min_length=1)
    delta_table: str
    uc_volume: str
    job: DatabricksJob = Field(default_factory=DatabricksJob)
    auth: DatabricksAuth

    @field_validator("delta_table")
    @classmethod
    def _valid_table(cls, v: str) -> str:
        if not _TABLE_RE.match(v):
            raise ValueError("delta_table must be 'catalog.schema.table' (alnum/underscore only)")
        return v

    @field_validator("uc_volume")
    @classmethod
    def _valid_volume(cls, v: str) -> str:
        if not _TABLE_RE.match(v):
            raise ValueError("uc_volume must be 'catalog.schema.volume' (alnum/underscore only)")
        return v


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    databricks: Databricks
    upload: Upload = Field(default_factory=Upload)
    buffer: Buffer = Field(default_factory=Buffer)
