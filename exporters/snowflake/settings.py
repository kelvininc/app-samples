import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")          # safe Snowflake identifier (db/schema/table)
_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")


# --- shared sub-models (Retry/Buffer identical across the DuckDB exporters; Upload varies per sink) ---

class Retry(BaseModel):
    # keyword default= (not positional) so the type checker sees these as optional fields.
    attempts: int = Field(default=3, ge=1)
    base_delay: float = Field(default=1.0, ge=0)
    max_delay: float = Field(default=30.0, ge=0)    # backoff ceiling, independent of upload.interval


class Upload(BaseModel):
    interval: int = Field(default=60, ge=1)         # ge=1: a 0s interval busy-spins the drain loop
    # le=4000: the multi-row INSERT binds 4 values/row and Snowflake caps binds per
    # statement at ~16,384, so a larger batch fails permanently (4 * 4000 = 16,000).
    batch_size: int = Field(default=1000, ge=1, le=4000)
    retry: Retry = Field(default_factory=Retry)     # default_factory: no shared mutable default


class Buffer(BaseModel):
    max_backlog: int = Field(default=1_000_000, ge=0)       # max un-uploaded rows kept before dropping oldest; 0 = unbounded (explicit opt-in)


# --- provider block (per-app: how to reach Snowflake + how to authenticate) ---

class SnowflakeAuth(BaseModel):
    # SecretStr so a stray log/repr of the config masks credentials instead of leaking them.
    method: Literal["password", "key_pair"] = "password"
    password: Optional[SecretStr] = None
    private_key: Optional[SecretStr] = None             # PEM-encoded RSA private key
    private_key_passphrase: Optional[SecretStr] = None  # optional, if the key is encrypted

    @field_validator("password", "private_key", "private_key_passphrase", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _one_auth(self) -> "SnowflakeAuth":
        if self.method == "password" and not self.password:
            raise ValueError("auth.method='password' requires password")
        if self.method == "key_pair" and not self.private_key:
            raise ValueError("auth.method='key_pair' requires private_key")
        return self


class Snowflake(BaseModel):
    # str_strip_whitespace so blank/whitespace identifiers fail the checks below.
    model_config = ConfigDict(str_strip_whitespace=True)

    account: str = Field(min_length=1)              # e.g. "xy12345.us-east-1"
    user: str = Field(min_length=1)
    warehouse: str = Field(min_length=1)
    database: str
    schema_: str = Field(alias="schema")            # "schema" shadows BaseModel.schema()
    table: str
    auth: SnowflakeAuth

    @field_validator("database", "schema_", "table")
    @classmethod
    def _valid_identifier(cls, v: str) -> str:
        if not _IDENT_RE.match(v):
            raise ValueError("database/schema/table must be a bare identifier (alnum/underscore)")
        return v


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    snowflake: Snowflake
    upload: Upload = Field(default_factory=Upload)
    buffer: Buffer = Field(default_factory=Buffer)
