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
    retry: Retry = Field(default_factory=Retry)     # default_factory: no shared mutable default


class Buffer(BaseModel):
    max_backlog: int = Field(default=0, ge=0)       # max un-uploaded rows kept before dropping oldest; 0 = unbounded


# --- provider block (per-app: how to reach Kafka + how to authenticate) ---
# The `security` dimension (protocol + sasl + tls) is shared with the Kafka importer; keep the
# two connectors' models in sync so one broker's config is copy-pasteable between them.

class KafkaSasl(BaseModel):
    # SecretStr so a stray log/repr of the config masks the password instead of leaking it.
    # All empty -> no SASL (PLAINTEXT/SSL); set all three together to authenticate.
    mechanism: Optional[Literal["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"]] = None
    username: Optional[str] = None
    password: Optional[SecretStr] = None

    @field_validator("password", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # *before* SecretStr wraps it, so the placeholder never becomes a "set" credential.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _all_or_nothing(self) -> "KafkaSasl":
        provided = [bool(self.mechanism), bool(self.username), bool(self.password)]
        if any(provided) and not all(provided):
            raise ValueError("SASL requires mechanism, username and password together (or none)")
        return self


class KafkaTls(BaseModel):
    # PEM *content*, not paths: config values arrive through the platform (wired to secrets),
    # never from the container filesystem. ca_cert set -> it REPLACES the system trust store
    # (private CA); client_cert+client_key together enable mTLS.
    ca_cert: str = ""
    client_cert: str = ""
    client_key: Optional[SecretStr] = None          # SecretStr: the private key must never hit a log/repr

    @field_validator("ca_cert", "client_cert", mode="before")
    @classmethod
    def _reject_unresolved_secret_str(cls, v: object) -> object:
        # Unresolved "<% secrets.x %>" placeholders normalize to unset ("" for the str fields).
        return "" if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @field_validator("client_key", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # Same normalization for the key, *before* SecretStr wraps it.
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @model_validator(mode="after")
    def _cert_and_key_together(self) -> "KafkaTls":
        if bool(self.client_cert) != bool(self.client_key):
            raise ValueError("mTLS requires client_cert and client_key together (or neither)")
        return self

    def configured(self) -> bool:
        """Whether any TLS material is set (drives the protocol-coherence check)."""
        return bool(self.ca_cert or self.client_cert or self.client_key)


class KafkaSecurity(BaseModel):
    protocol: Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"] = "PLAINTEXT"
    sasl: KafkaSasl = Field(default_factory=KafkaSasl)
    tls: KafkaTls = Field(default_factory=KafkaTls)

    @model_validator(mode="after")
    def _blocks_match_protocol(self) -> "KafkaSecurity":
        # Keep protocol and the sasl/tls blocks coherent so a mismatch surfaces as a recoverable
        # ValidationError, not an uncaught aiokafka ValueError that crashes the deployment.
        is_sasl = self.protocol.startswith("SASL")
        if is_sasl and not self.sasl.mechanism:
            raise ValueError(f"protocol {self.protocol} requires a sasl block")
        if not is_sasl and self.sasl.mechanism:
            raise ValueError(f"sasl is set but protocol {self.protocol} is not SASL_*")
        if self.tls.configured() and self.protocol not in ("SSL", "SASL_SSL"):
            raise ValueError(f"tls is set but protocol {self.protocol} is not SSL/SASL_SSL")
        return self


class Kafka(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # Required, no default (family convention: exporters fail fast on an unconfigured
    # deployment); the importer keeps a working localhost default, per the importer family.
    bootstrap_servers: str = Field(min_length=1)    # comma-separated host:port list
    client_id: str = Field(default="kelvin-kafka-exporter", min_length=1)
    security: KafkaSecurity = Field(default_factory=KafkaSecurity)


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    kafka: Kafka
    upload: Upload = Field(default_factory=Upload)
    buffer: Buffer = Field(default_factory=Buffer)
