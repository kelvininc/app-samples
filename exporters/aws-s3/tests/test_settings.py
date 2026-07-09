"""Unit tests for the nested Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

S3 = {"region": "us-east-1", "bucket": "telemetry"}
KEYS = {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "shhh"}
FULL = {"s3": {**S3, "auth": KEYS}}


class TestAuth:
    """S3 credential validation."""

    def test_accepts_explicit_keys(self) -> None:
        """Both keys present parse and are exposed via get_secret_value()."""
        s = Settings(**FULL)
        assert s.s3.auth.access_key_id.get_secret_value() == "AKIAEXAMPLE"
        assert s.s3.auth.secret_access_key.get_secret_value() == "shhh"

    def test_accepts_default_chain_when_both_empty(self) -> None:
        """Neither key set is valid: boto3 falls back to the AWS default credential chain."""
        s = Settings(s3=S3)
        assert s.s3.auth.access_key_id is None and s.s3.auth.secret_access_key is None

    @pytest.mark.parametrize("partial", [{"access_key_id": "k"}, {"secret_access_key": "s"}])
    def test_rejects_one_key_without_the_other(self, partial: dict) -> None:
        """One key without the other is a misconfig (not the default chain, not full creds)."""
        with pytest.raises(ValidationError, match="both access_key_id and secret_access_key"):
            Settings(s3={**S3, "auth": partial})

    def test_credentials_are_masked_in_repr(self) -> None:
        """SecretStr keeps keys out of repr/log output, readable via get_secret_value()."""
        secret = "super-secret-xyz"
        auth = Settings(s3={**S3, "auth": {"access_key_id": "k", "secret_access_key": secret}}).s3.auth
        assert secret not in repr(auth)
        assert auth.secret_access_key.get_secret_value() == secret

    def test_unresolved_secret_is_treated_as_unset(self) -> None:
        """A never-resolved '<% secrets.x %>' literal normalizes to None (so it isn't 'set')."""
        s = Settings(s3={**S3, "auth": {"access_key_id": "<% secrets.aws-access-key-id %>",
                                        "secret_access_key": "<% secrets.aws-secret-access-key %>"}})
        assert s.s3.auth.access_key_id is None and s.s3.auth.secret_access_key is None


class TestS3Block:
    """region / bucket presence."""

    @pytest.mark.parametrize("field", ["region", "bucket"])
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_blank_region_or_bucket(self, field: str, blank: str) -> None:
        """A blank or whitespace region/bucket is rejected at config time."""
        with pytest.raises(ValidationError):
            Settings(s3={**S3, field: blank})


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
