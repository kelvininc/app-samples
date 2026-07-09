"""Unit tests for the nested Settings model (Zerobus: oauth-only databricks block)."""
import pytest
from pydantic import ValidationError

from settings import Settings

DB = {"server_hostname": "h", "zerobus_endpoint": "ep", "delta_table": "c.s.t"}
OAUTH = {"databricks": {**DB, "auth": {"client_id": "id", "client_secret": "sec"}}}


class TestAuth:
    """OAuth credential validation in the databricks block."""

    def test_accepts_oauth(self) -> None:
        """A valid oauth config parses and exposes the table + client credentials."""
        s = Settings(**OAUTH)
        assert s.databricks.delta_table == "c.s.t"
        assert s.databricks.auth.client_id.get_secret_value() == "id"
        assert s.databricks.auth.client_secret.get_secret_value() == "sec"

    @pytest.mark.parametrize("missing", [
        {"client_id": "id"},                    # no client_secret
        {"client_secret": "sec"},               # no client_id
        {},                                     # neither
    ])
    def test_requires_both_credentials(self, missing: dict) -> None:
        """auth requires BOTH client_id and client_secret."""
        with pytest.raises(ValidationError, match="requires client_id and client_secret"):
            Settings(databricks={**DB, "auth": missing})

    def test_credentials_are_masked_in_repr(self) -> None:
        """SecretStr keeps the secret out of repr/log output, readable via get_secret_value()."""
        secret = "super-secret-xyz"
        auth = Settings(databricks={**DB, "auth": {"client_id": "id",
                                                   "client_secret": secret}}).databricks.auth
        assert secret not in repr(auth)
        assert auth.client_secret.get_secret_value() == secret

    def test_unresolved_secret_is_treated_as_unset(self) -> None:
        """A never-resolved '<% secrets.x %>' literal normalizes to None and fails the require-both."""
        with pytest.raises(ValidationError, match="requires client_id and client_secret"):
            Settings(databricks={**DB, "auth": {"client_id": "id",
                                                "client_secret": "<% secrets.databricks-client-secret %>"}})


class TestTable:
    """delta_table identifier validation (kills identifier injection)."""

    @pytest.mark.parametrize("bad_table", ["c.s.t; DROP TABLE x", "schema.table", "a.b.c.d", "a..b"])
    def test_rejects_malformed_table(self, bad_table: str) -> None:
        """delta_table must be exactly catalog.schema.table (alnum/underscore)."""
        with pytest.raises(ValidationError, match="catalog.schema.table"):
            Settings(databricks={**DB, "delta_table": bad_table,
                                 "auth": {"client_id": "id", "client_secret": "sec"}})


class TestHost:
    """server_hostname / zerobus_endpoint must be present and non-blank (fail fast at config time)."""

    @pytest.mark.parametrize("field", ["server_hostname", "zerobus_endpoint"])
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_host_or_endpoint(self, field: str, blank: str) -> None:
        """A blank or whitespace host/endpoint is rejected, not deferred to a connection error."""
        with pytest.raises(ValidationError):
            Settings(databricks={**DB, field: blank,
                                 "auth": {"client_id": "id", "client_secret": "sec"}})


class TestUploadAndBuffer:
    """Upload/buffer knobs: coercion, bounds, defaults."""

    def test_coerces_numeric_strings(self) -> None:
        """Numeric strings from config are coerced to int."""
        s = Settings(**OAUTH, upload={"batch_size": "500", "interval": "10"})
        assert s.upload.batch_size == 500 and s.upload.interval == 10

    @pytest.mark.parametrize("bad", [{"batch_size": 0}, {"batch_size": -1}, {"interval": 0}, {"interval": -1}])
    def test_rejects_out_of_bounds(self, bad: dict) -> None:
        """Upload knobs enforce their ge= bounds (interval ge=1: 0 would busy-spin the drain)."""
        with pytest.raises(ValidationError):
            Settings(**OAUTH, upload=bad)

    def test_defaults(self) -> None:
        """Upload, retry, and buffer have sensible defaults when omitted."""
        s = Settings(**OAUTH)
        assert s.upload.batch_size == 1000 and s.upload.interval == 60
        assert s.upload.retry.attempts == 3 and s.upload.retry.max_delay == 30.0
        assert s.buffer.max_backlog == 0


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = Settings(**OAUTH, some_platform_key="x")
    assert s.upload.batch_size == 1000
