"""Unit tests for the nested SFTP Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

SFTP = {"host": "sftp.example.com", "username": "svc"}
PW = {"sftp": {**SFTP, "auth": {"method": "password", "password": "pw"}}}
KEY = {"sftp": {**SFTP, "auth": {"method": "private_key", "private_key": "-----BEGIN KEY-----"}}}


class TestAuth:
    def test_accepts_password(self) -> None:
        s = Settings(**PW)
        assert s.sftp.auth.method == "password" and s.sftp.auth.password.get_secret_value() == "pw"

    def test_accepts_private_key(self) -> None:
        assert Settings(**KEY).sftp.auth.private_key.get_secret_value() == "-----BEGIN KEY-----"

    def test_rejects_password_method_without_password(self) -> None:
        with pytest.raises(ValidationError, match="requires password"):
            Settings(sftp={**SFTP, "auth": {"method": "password"}})

    def test_rejects_private_key_method_without_key(self) -> None:
        with pytest.raises(ValidationError, match="requires private_key"):
            Settings(sftp={**SFTP, "auth": {"method": "private_key"}})

    def test_unresolved_secret_is_treated_as_unset(self) -> None:
        with pytest.raises(ValidationError, match="requires password"):
            Settings(sftp={**SFTP, "auth": {"method": "password", "password": "<% secrets.sftp-password %>"}})

    def test_credentials_are_masked_in_repr(self) -> None:
        secret = "super-secret-pw"
        auth = Settings(sftp={**SFTP, "auth": {"method": "password", "password": secret}}).sftp.auth
        assert secret not in repr(auth)


class TestConnection:
    def test_defaults(self) -> None:
        s = Settings(**PW)
        assert s.sftp.port == 22 and s.sftp.remote_dir == "." and s.sftp.verify_host_key is True
        assert s.sftp.known_hosts is None and s.sftp.timeout == 30

    @pytest.mark.parametrize("bad_timeout", [0, -1])
    def test_rejects_non_positive_timeout(self, bad_timeout: float) -> None:
        with pytest.raises(ValidationError):
            Settings(sftp={**SFTP, "timeout": bad_timeout, "auth": {"method": "password", "password": "pw"}})

    @pytest.mark.parametrize("field", ["host", "username"])
    def test_rejects_blank(self, field: str) -> None:
        with pytest.raises(ValidationError):
            Settings(sftp={**SFTP, field: "  ", "auth": {"method": "password", "password": "pw"}})

    @pytest.mark.parametrize("bad_port", [0, 70000])
    def test_rejects_out_of_range_port(self, bad_port: int) -> None:
        with pytest.raises(ValidationError):
            Settings(sftp={**SFTP, "port": bad_port, "auth": {"method": "password", "password": "pw"}})


class TestUploadAndBuffer:
    def test_defaults_and_format(self) -> None:
        s = Settings(**PW)
        assert s.upload.format == "parquet" and s.upload.batch_size == 1000
        assert s.buffer.max_backlog == 1_000_000        # bounded by default; 0 is explicit opt-in to unbounded

    def test_max_backlog_zero_is_allowed_as_unbounded(self) -> None:
        assert Settings(**PW, buffer={"max_backlog": 0}).buffer.max_backlog == 0

    def test_rejects_unknown_format(self) -> None:
        with pytest.raises(ValidationError):
            Settings(**PW, upload={"format": "avro"})

    @pytest.mark.parametrize("bad", [{"batch_size": 0}, {"interval": 0}])
    def test_rejects_out_of_bounds(self, bad: dict) -> None:
        with pytest.raises(ValidationError):
            Settings(**PW, upload=bad)


def test_ignores_unknown_top_level_keys() -> None:
    assert Settings(**PW, some_platform_key="x").upload.batch_size == 1000
