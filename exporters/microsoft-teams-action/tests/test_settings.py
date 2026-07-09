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


def test_missing_teams_block_fails() -> None:
    """The teams provider block is required."""
    with pytest.raises(ValidationError):
        Settings()


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = Settings(teams={"webhook_url": URL}, some_platform_key="x")
    assert s.teams.webhook_url.get_secret_value() == URL
