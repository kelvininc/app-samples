"""Unit tests for the nested Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

ADLS = {"account_name": "telemetrylake", "container": "raw"}
FULL = {"adls": {**ADLS, "auth": {"account_key": "shhh"}}}


class TestAuth:
    """ADLS account-key validation."""

    def test_accepts_account_key(self) -> None:
        """A present key parses and is exposed via get_secret_value()."""
        s = Settings(**FULL)
        assert s.adls.auth.account_key.get_secret_value() == "shhh"

    def test_no_key_is_unset(self) -> None:
        """Omitting auth leaves account_key None (no credential set)."""
        s = Settings(adls=ADLS)
        assert s.adls.auth.account_key is None

    def test_credentials_are_masked_in_repr(self) -> None:
        """SecretStr keeps the key out of repr/log output, readable via get_secret_value()."""
        secret = "super-secret-xyz"
        auth = Settings(adls={**ADLS, "auth": {"account_key": secret}}).adls.auth
        assert secret not in repr(auth)
        assert auth.account_key.get_secret_value() == secret

    def test_unresolved_secret_is_treated_as_unset(self) -> None:
        """A never-resolved '<% secrets.x %>' literal normalizes to None (so it isn't 'set')."""
        s = Settings(adls={**ADLS, "auth": {"account_key": "<% secrets.azure-account-key %>"}})
        assert s.adls.auth.account_key is None


class TestADLSBlock:
    """account_name / container presence."""

    @pytest.mark.parametrize("field", ["account_name", "container"])
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_account_or_container(self, field: str, blank: str) -> None:
        """A blank or whitespace account_name/container is rejected at config time."""
        with pytest.raises(ValidationError):
            Settings(adls={**ADLS, field: blank})

    @pytest.mark.parametrize("bad", ["ab", "a" * 25, "Telemetry", "my-lake", "acct.attacker.example/x"])
    def test_rejects_malformed_account_name(self, bad: str) -> None:
        """account_name must be 3-24 lowercase alphanumerics (it's interpolated into the account URL)."""
        with pytest.raises(ValidationError):
            Settings(adls={**ADLS, "account_name": bad})

    def test_rejects_missing_container(self) -> None:
        """container is required."""
        with pytest.raises(ValidationError):
            Settings(adls={"account_name": "telemetrylake"})


class TestUploadAndBuffer:
    """Upload/buffer knobs: format, coercion, bounds, defaults."""

    def test_defaults(self) -> None:
        """Upload, retry, and buffer have sensible defaults when omitted."""
        s = Settings(**FULL)
        assert s.upload.batch_size == 1000 and s.upload.interval == 60 and s.upload.format == "parquet"
        assert s.upload.retry.attempts == 3 and s.buffer.max_backlog == 0

    def test_accepts_known_formats(self) -> None:
        """format accepts the three DuckDB COPY outputs."""
        assert Settings(**FULL, upload={"format": "csv"}).upload.format == "csv"

    def test_rejects_unknown_format(self) -> None:
        """An unsupported file format is rejected."""
        with pytest.raises(ValidationError):
            Settings(**FULL, upload={"format": "avro"})

    @pytest.mark.parametrize("bad", [{"batch_size": 0}, {"batch_size": -1}, {"interval": 0}, {"interval": -1}])
    def test_rejects_out_of_bounds(self, bad: dict) -> None:
        """Upload knobs enforce their ge= bounds (interval ge=1: 0 would busy-spin the drain)."""
        with pytest.raises(ValidationError):
            Settings(**FULL, upload=bad)


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    assert Settings(**FULL, some_platform_key="x").upload.batch_size == 1000
