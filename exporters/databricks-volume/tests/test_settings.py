"""Unit tests for the nested Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

DB = {"server_hostname": "dbc-123.cloud.databricks.com",
      "delta_table": "main.telemetry.readings",
      "uc_volume": "main.telemetry.landing"}
OAUTH = {"method": "oauth", "client_id": "cid", "client_secret": "csec"}
TOKEN = {"method": "access_token", "access_token": "tok"}
FULL = {"databricks": {**DB, "auth": OAUTH}}


class TestAuth:
    """Databricks credential validation (one-auth + masking)."""

    def test_oauth_requires_id_and_secret(self) -> None:
        """oauth method parses with both credentials and exposes them via get_secret_value()."""
        s = Settings(**FULL)
        assert s.databricks.auth.client_id.get_secret_value() == "cid"
        assert s.databricks.auth.client_secret.get_secret_value() == "csec"

    def test_access_token_requires_token(self) -> None:
        """access_token method parses with a token."""
        s = Settings(databricks={**DB, "auth": TOKEN})
        assert s.databricks.auth.access_token.get_secret_value() == "tok"

    @pytest.mark.parametrize("bad_auth", [
        {"method": "oauth", "client_id": "cid"},                 # missing secret
        {"method": "oauth", "client_secret": "csec"},            # missing id
        {"method": "oauth"},                                     # missing both
    ])
    def test_oauth_rejects_missing_credentials(self, bad_auth: dict) -> None:
        """oauth without both client_id and client_secret is rejected."""
        with pytest.raises(ValidationError, match="requires client_id and client_secret"):
            Settings(databricks={**DB, "auth": bad_auth})

    def test_access_token_rejects_missing_token(self) -> None:
        """access_token method without a token is rejected."""
        with pytest.raises(ValidationError, match="requires access_token"):
            Settings(databricks={**DB, "auth": {"method": "access_token"}})

    def test_credentials_are_masked_in_repr(self) -> None:
        """SecretStr keeps credentials out of repr/log output, readable via get_secret_value()."""
        secret = "super-secret-xyz"
        auth = Settings(databricks={**DB, "auth": {"method": "access_token",
                                                   "access_token": secret}}).databricks.auth
        assert secret not in repr(auth)
        assert auth.access_token.get_secret_value() == secret

    def test_unresolved_secret_is_treated_as_unset(self) -> None:
        """A never-resolved '<% secrets.x %>' literal normalizes to None (so oauth fails loudly)."""
        with pytest.raises(ValidationError, match="requires client_id and client_secret"):
            Settings(databricks={**DB, "auth": {"method": "oauth",
                                                "client_id": "<% secrets.databricks-client-id %>",
                                                "client_secret": "<% secrets.databricks-client-secret %>"}})


class TestDatabricksBlock:
    """server_hostname / delta_table / uc_volume / job validation."""

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_server_hostname(self, blank: str) -> None:
        """A blank or whitespace server_hostname is rejected at config time."""
        with pytest.raises(ValidationError):
            Settings(databricks={**DB, "server_hostname": blank, "auth": OAUTH})

    @pytest.mark.parametrize("bad_table", ["c.s.t; DROP TABLE x", "schema.table", "a.b.c.d", "a.b"])
    def test_rejects_malformed_delta_table(self, bad_table: str) -> None:
        """delta_table must be exactly catalog.schema.table (alnum/underscore)."""
        with pytest.raises(ValidationError, match="catalog.schema.table"):
            Settings(databricks={**DB, "delta_table": bad_table, "auth": OAUTH})

    @pytest.mark.parametrize("bad_volume", ["c.s.v; DROP", "schema.volume", "a.b.c.d", "a.b"])
    def test_rejects_malformed_uc_volume(self, bad_volume: str) -> None:
        """uc_volume must be exactly catalog.schema.volume (alnum/underscore)."""
        with pytest.raises(ValidationError, match="catalog.schema.volume"):
            Settings(databricks={**DB, "uc_volume": bad_volume, "auth": OAUTH})

    def test_job_is_optional(self) -> None:
        """job defaults to no cluster_id/warehouse_id (upload only)."""
        s = Settings(**FULL)
        assert s.databricks.job.cluster_id is None and s.databricks.job.warehouse_id is None

    def test_job_accepts_ids(self) -> None:
        """job carries cluster_id/warehouse_id when provided."""
        s = Settings(databricks={**DB, "auth": OAUTH, "job": {"warehouse_id": "wh-1"}})
        assert s.databricks.job.warehouse_id == "wh-1" and s.databricks.job.cluster_id is None


class TestUploadAndBuffer:
    """Upload/buffer knobs: format, bounds, defaults."""

    def test_defaults(self) -> None:
        """Upload, retry, and buffer have sensible defaults when omitted."""
        s = Settings(**FULL)
        assert s.upload.batch_size == 1000 and s.upload.interval == 60 and s.upload.format == "parquet"
        assert s.upload.retry.attempts == 3
        # buffer is bounded by default (1M rows): a prolonged outage drops oldest instead of
        # filling the disk; 0 (unbounded) is an explicit opt-in.
        assert s.buffer.max_backlog == 1_000_000

    def test_max_backlog_unbounded_opt_in(self) -> None:
        """max_backlog=0 is accepted as the explicit unbounded opt-in."""
        assert Settings(**FULL, buffer={"max_backlog": 0}).buffer.max_backlog == 0

    def test_accepts_known_formats(self) -> None:
        """format accepts parquet/csv (the re-ingestable, text-payload outputs)."""
        assert Settings(**FULL, upload={"format": "csv"}).upload.format == "csv"

    @pytest.mark.parametrize("bad", ["json", "avro"])
    def test_rejects_unsupported_format(self, bad: str) -> None:
        """json/other are rejected: the volume ingestion job only round-trips text-payload formats."""
        with pytest.raises(ValidationError):
            Settings(**FULL, upload={"format": bad})

    @pytest.mark.parametrize("bad", [{"batch_size": 0}, {"batch_size": -1}, {"interval": 0}, {"interval": -1}])
    def test_rejects_out_of_bounds(self, bad: dict) -> None:
        """Upload knobs enforce their ge= bounds (interval ge=1: 0 would busy-spin the drain)."""
        with pytest.raises(ValidationError):
            Settings(**FULL, upload=bad)


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    assert Settings(**FULL, some_platform_key="x").upload.batch_size == 1000
