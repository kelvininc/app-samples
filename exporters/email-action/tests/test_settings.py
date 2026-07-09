"""Unit tests for the SMTP Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

SMTP = {"host": "smtp.example.com", "from_address": "alerts@example.com"}


def test_defaults() -> None:
    """Port/TLS have sensible defaults; auth defaults to method='none' (unauthenticated relay)."""
    s = Settings(smtp=SMTP)
    assert s.smtp.port == 587 and s.smtp.use_tls is True
    assert s.smtp.auth.method == "none"
    assert s.smtp.auth.username is None and s.smtp.auth.password is None


def test_method_none_needs_no_credentials() -> None:
    """An explicit method='none' with no credentials is a valid unauthenticated relay config."""
    s = Settings(smtp={**SMTP, "auth": {"method": "none"}})
    assert s.smtp.auth.method == "none"


def test_method_none_ignores_supplied_credentials() -> None:
    """Credentials under method='none' are accepted but unused (sftp policy: only the selected
    method's credentials are validated; extras aren't rejected)."""
    s = Settings(smtp={**SMTP, "auth": {"method": "none", "username": "u", "password": "p"}})
    assert s.smtp.auth.method == "none" and s.smtp.auth.username == "u"


def test_accepts_username_password_and_masks_password() -> None:
    """method='username_password' with both credentials is valid; the password is a masked SecretStr."""
    s = Settings(smtp={**SMTP, "auth": {"method": "username_password", "username": "u", "password": "shhh"}})
    assert s.smtp.auth.method == "username_password"
    assert s.smtp.auth.password.get_secret_value() == "shhh"
    assert "shhh" not in repr(s.smtp.auth)


@pytest.mark.parametrize("partial", [{}, {"username": "u"}, {"password": "p"}])
def test_rejects_username_password_method_without_both(partial: dict) -> None:
    """method='username_password' requires BOTH credentials."""
    with pytest.raises(ValidationError, match="requires username and password"):
        Settings(smtp={**SMTP, "auth": {"method": "username_password", **partial}})


@pytest.mark.parametrize("unwired", [
    {"username": "u", "password": "<% secrets.smtp-password %>"},
    {"username": "<% secrets.smtp-username %>", "password": "p"},
])
def test_one_unresolved_secret_is_treated_as_unset(unwired: dict) -> None:
    """An unresolved '<% secrets.x %>' credential normalizes to None, failing the method check."""
    with pytest.raises(ValidationError, match="requires username and password"):
        Settings(smtp={**SMTP, "auth": {"method": "username_password", **unwired}})


def test_both_unresolved_secrets_fail_validation() -> None:
    """Both credentials un-wired under method='username_password' -> both None -> hard failure
    (not a silent fallback to an unauthenticated send)."""
    with pytest.raises(ValidationError, match="requires username and password"):
        Settings(smtp={**SMTP, "auth": {"method": "username_password",
                                        "username": "<% secrets.smtp-username %>",
                                        "password": "<% secrets.smtp-password %>"}})


@pytest.mark.parametrize("field", ["host", "from_address"])
def test_rejects_blank_required_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(smtp={**SMTP, field: "  "})


@pytest.mark.parametrize("bad_port", [0, 70000])
def test_rejects_out_of_range_port(bad_port: int) -> None:
    with pytest.raises(ValidationError):
        Settings(smtp={**SMTP, "port": bad_port})


def test_missing_smtp_block_fails() -> None:
    with pytest.raises(ValidationError):
        Settings()


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = Settings(smtp=SMTP, some_platform_key="x")
    assert s.smtp.host == "smtp.example.com"
