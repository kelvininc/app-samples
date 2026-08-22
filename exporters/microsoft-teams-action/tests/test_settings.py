"""Unit tests for the Teams Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings

URL = "https://example.webhook.office.com/webhookb2/abc/IncomingWebhook/def"


def test_accepts_webhook_url() -> None:
    """A valid webhook URL parses and is exposed via get_secret_value()."""
    s = Settings(teams={"webhook_url": URL})
    assert s.teams.webhook_url.get_secret_value() == URL


def test_webhook_url_is_masked_in_repr() -> None:
    """SecretStr keeps the URL (which embeds a token) out of repr/log output."""
    s = Settings(teams={"webhook_url": URL})
    assert URL not in repr(s.teams)
    assert s.teams.webhook_url.get_secret_value() == URL


def test_unresolved_secret_fails_fast() -> None:
    """A never-resolved '<% secrets.x %>' literal normalizes to unset, so the required field fails."""
    with pytest.raises(ValidationError):
        Settings(teams={"webhook_url": "<% secrets.teams-webhook-url %>"})


def test_blank_webhook_url_fails() -> None:
    """A blank webhook URL fails validation instead of deferring the failure to the first send."""
    with pytest.raises(ValidationError):
        Settings(teams={"webhook_url": ""})


def test_missing_webhook_url_fails() -> None:
    """The webhook URL is required."""
    with pytest.raises(ValidationError):
        Settings(teams={})


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com/hook", "example.com/hook"])
def test_rejects_non_http_url(url: str) -> None:
    """The webhook must be an http(s) URL, so a typo fails at config time, not on first send."""
    with pytest.raises(ValidationError):
        Settings(teams={"webhook_url": url})


def test_url_validation_error_does_not_leak_the_url() -> None:
    """The rejection reason carries no part of the URL, which embeds a token.

    This pins the shape main.py actually logs (`include_input=False`). Pydantic records the raw
    input on the error regardless, so `str(exc)` does contain the URL -- which is exactly why the
    app must never report a configuration error that way.
    """
    with pytest.raises(ValidationError) as excinfo:
        Settings(teams={"webhook_url": "ftp://example.com/hook?token=SUPERSECRETVALUE"})
    logged = str(excinfo.value.errors(include_url=False, include_input=False))
    assert "SUPERSECRETVALUE" not in logged
    assert "must be a valid http(s) URL" in logged


def test_missing_teams_block_fails() -> None:
    """The teams provider block is required."""
    with pytest.raises(ValidationError):
        Settings()


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = Settings(teams={"webhook_url": URL}, some_platform_key="x")
    assert s.teams.webhook_url.get_secret_value() == URL
