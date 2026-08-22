"""Unit tests for the nested Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

DB = {"server_hostname": "h", "http_path": "/p", "delta_table": "c.s.t"}
TOKEN = {"databricks": {**DB, "auth": {"method": "access_token", "access_token": "tok"}}}
OAUTH = {"databricks": {**DB, "auth": {"method": "oauth", "client_id": "id", "client_secret": "sec"}}}


class TestAuth:
    """Authentication validation in the databricks block."""

    def test_accepts_access_token(self) -> None:
        """A valid access_token config parses and exposes the table."""
        s = Settings(**TOKEN)
        assert s.databricks.delta_table == "c.s.t" and s.databricks.auth.method == "access_token"

    def test_accepts_oauth(self) -> None:
        """A valid oauth config parses with client credentials."""
        assert Settings(**OAUTH).databricks.auth.client_id.get_secret_value() == "id"

    def test_credentials_are_masked_in_repr(self) -> None:
        """SecretStr keeps the token out of repr/log output, readable via get_secret_value()."""
        secret = "super-secret-xyz"
        auth = Settings(databricks={**DB, "auth": {"method": "access_token",
                                                   "access_token": secret}}).databricks.auth
        assert secret not in repr(auth)
        assert auth.access_token.get_secret_value() == secret

    def test_rejects_oauth_without_credentials(self) -> None:
        """oauth requires both client_id and client_secret."""
        with pytest.raises(ValidationError, match="requires client_id and client_secret"):
            Settings(databricks={**DB, "auth": {"method": "oauth"}})

    def test_rejects_token_method_without_token(self) -> None:
        """access_token method requires an access_token value."""
        with pytest.raises(ValidationError, match="requires access_token"):
            Settings(databricks={**DB, "auth": {"method": "access_token"}})

    def test_unresolved_secret_is_treated_as_unset(self) -> None:
        """A never-resolved '<% secrets.x %>' literal normalizes to None and fails one-auth."""
        with pytest.raises(ValidationError, match="requires access_token"):
            Settings(databricks={**DB, "auth": {"method": "access_token",
                                                "access_token": "<% secrets.databricks-access-token %>"}})

    def test_rejects_unknown_method(self) -> None:
        """auth.method only accepts the two known discriminator values."""
        with pytest.raises(ValidationError):
            Settings(databricks={**DB, "auth": {"method": "kerberos", "access_token": "tok"}})


class TestTable:
    """delta_table identifier validation (kills identifier injection)."""

    @pytest.mark.parametrize("bad_table", ["c.s.t; DROP TABLE x", "schema.table", "a.b.c.d", "a..b"])
    def test_rejects_malformed_table(self, bad_table: str) -> None:
        """delta_table must be exactly catalog.schema.table (alnum/underscore)."""
        with pytest.raises(ValidationError, match="catalog.schema.table"):
            Settings(databricks={**DB, "delta_table": bad_table,
                                 "auth": {"method": "access_token", "access_token": "tok"}})


class TestHost:
    """server_hostname / http_path must be present and non-blank (fail fast at config time)."""

    @pytest.mark.parametrize("field", ["server_hostname", "http_path"])
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_host_or_path(self, field: str, blank: str) -> None:
        """A blank or whitespace host/path is rejected, not deferred to a connection error."""
        with pytest.raises(ValidationError):
            Settings(databricks={**DB, field: blank,
                                 "auth": {"method": "access_token", "access_token": "tok"}})


class TestUploadAndBuffer:
    """Upload/buffer knobs: coercion, bounds, defaults."""

    def test_coerces_numeric_strings(self) -> None:
        """Numeric strings from config are coerced to int."""
        s = Settings(**TOKEN, upload={"batch_size": "500", "interval": "10"})
        assert s.upload.batch_size == 500 and s.upload.interval == 10

    @pytest.mark.parametrize("bad", [{"batch_size": 0}, {"batch_size": -1}, {"interval": 0}, {"interval": -1}])
    def test_rejects_out_of_bounds(self, bad: dict) -> None:
        """Upload knobs enforce their ge= bounds (interval ge=1: 0 would busy-spin the drain)."""
        with pytest.raises(ValidationError):
            Settings(**TOKEN, upload=bad)

    def test_defaults(self) -> None:
        """Upload, retry, and buffer have sensible defaults when omitted."""
        s = Settings(**TOKEN)
        assert s.upload.batch_size == 1000 and s.upload.interval == 60
        assert s.upload.retry.attempts == 3 and s.upload.retry.max_delay == 30.0
        assert s.buffer.max_backlog == 1_000_000       # bounded by default; 0 = unbounded opt-in

    def test_buffer_unbounded_opt_in(self) -> None:
        """max_backlog=0 is still accepted as the explicit unbounded opt-in."""
        assert Settings(**TOKEN, buffer={"max_backlog": 0}).buffer.max_backlog == 0


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = Settings(**TOKEN, some_platform_key="x")
    assert s.upload.batch_size == 1000
