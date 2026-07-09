"""Unit tests for the nested Snowflake Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

SF = {"account": "xy12345.us-east-1", "user": "svc", "warehouse": "WH",
      "database": "DB", "schema": "PUBLIC", "table": "EVENTS"}
PW = {"snowflake": {**SF, "auth": {"method": "password", "password": "pw"}}}
KP = {"snowflake": {**SF, "auth": {"method": "key_pair", "private_key": "-----BEGIN KEY-----"}}}


class TestAuth:
    """Authentication validation in the snowflake block."""

    def test_accepts_password(self) -> None:
        s = Settings(**PW)
        assert s.snowflake.auth.method == "password"
        assert s.snowflake.auth.password.get_secret_value() == "pw"

    def test_accepts_key_pair(self) -> None:
        assert Settings(**KP).snowflake.auth.private_key.get_secret_value() == "-----BEGIN KEY-----"

    def test_rejects_password_method_without_password(self) -> None:
        with pytest.raises(ValidationError, match="requires password"):
            Settings(snowflake={**SF, "auth": {"method": "password"}})

    def test_rejects_key_pair_method_without_key(self) -> None:
        with pytest.raises(ValidationError, match="requires private_key"):
            Settings(snowflake={**SF, "auth": {"method": "key_pair"}})

    def test_unresolved_secret_is_treated_as_unset(self) -> None:
        with pytest.raises(ValidationError, match="requires password"):
            Settings(snowflake={**SF, "auth": {"method": "password",
                                               "password": "<% secrets.snowflake-password %>"}})

    def test_credentials_are_masked_in_repr(self) -> None:
        secret = "super-secret-pw"
        auth = Settings(snowflake={**SF, "auth": {"method": "password", "password": secret}}).snowflake.auth
        assert secret not in repr(auth)


class TestIdentifiers:
    """database/schema/table must be safe identifiers (kills SQL injection in the FQN)."""

    @pytest.mark.parametrize("field", ["database", "schema", "table"])
    @pytest.mark.parametrize("bad", ["a.b", "ev;DROP", "has space", ""])
    def test_rejects_bad_identifier(self, field: str, bad: str) -> None:
        with pytest.raises(ValidationError):
            Settings(snowflake={**SF, field: bad, "auth": {"method": "password", "password": "pw"}})

    def test_schema_alias(self) -> None:
        """The 'schema' config key maps to schema_ (which avoids shadowing BaseModel.schema)."""
        assert Settings(**PW).snowflake.schema_ == "PUBLIC"


class TestConnection:
    """Connection identity fields."""

    @pytest.mark.parametrize("field", ["account", "user", "warehouse"])
    def test_rejects_blank(self, field: str) -> None:
        with pytest.raises(ValidationError):
            Settings(snowflake={**SF, field: "  ", "auth": {"method": "password", "password": "pw"}})


class TestUploadAndBuffer:
    def test_defaults_and_bounds(self) -> None:
        s = Settings(**PW)
        assert s.upload.batch_size == 1000 and s.upload.interval == 60 and s.buffer.max_backlog == 0

    @pytest.mark.parametrize("bad", [{"batch_size": 0}, {"interval": 0}, {"interval": -1}])
    def test_rejects_out_of_bounds(self, bad: dict) -> None:
        with pytest.raises(ValidationError):
            Settings(**PW, upload=bad)


def test_ignores_unknown_top_level_keys() -> None:
    assert Settings(**PW, some_platform_key="x").upload.batch_size == 1000
