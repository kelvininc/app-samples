"""Unit tests for the Slack Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings


def test_accepts_token() -> None:
    """A valid token parses and is exposed via get_secret_value()."""
    s = Settings(slack={"token": "xoxb-real-token"})
    assert s.slack.token.get_secret_value() == "xoxb-real-token"


def test_token_is_masked_in_repr() -> None:
    """SecretStr keeps the token out of repr/log output."""
    secret = "xoxb-super-secret"
    s = Settings(slack={"token": secret})
    assert secret not in repr(s.slack)
    assert s.slack.token.get_secret_value() == secret


def test_unresolved_secret_fails_fast() -> None:
    """A never-resolved '<% secrets.x %>' literal normalizes to unset, so the required token fails."""
    with pytest.raises(ValidationError):
        Settings(slack={"token": "<% secrets.slack-bot-token %>"})


def test_missing_token_fails() -> None:
    """The token is required."""
    with pytest.raises(ValidationError):
        Settings(slack={})


def test_missing_slack_block_fails() -> None:
    """The slack provider block is required."""
    with pytest.raises(ValidationError):
        Settings()


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = Settings(slack={"token": "xoxb-t"}, some_platform_key="x")
    assert s.slack.token.get_secret_value() == "xoxb-t"
