"""Unit tests for the Teams Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings, normalize_channel

URL = "https://example.webhook.office.com/webhookb2/abc/IncomingWebhook/def"
OTHER_URL = "https://example.webhook.office.com/webhookb2/ghi/IncomingWebhook/jkl"


def _settings(*webhooks: dict, **extra: object) -> Settings:
    return Settings(teams={"webhooks": list(webhooks)}, **extra)


def test_accepts_a_webhook() -> None:
    """A valid entry parses and its URL is exposed via get_secret_value()."""
    s = _settings({"channel": "alerts", "url": URL})
    assert s.teams.webhooks[0].channel == "alerts"
    assert s.teams.webhooks[0].url.get_secret_value() == URL


def test_accepts_several_channels() -> None:
    """One workload can hold a webhook per channel; each keeps its own URL."""
    s = _settings({"channel": "alerts", "url": URL}, {"channel": "ops", "url": OTHER_URL})
    assert [w.channel for w in s.teams.webhooks] == ["alerts", "ops"]
    assert s.teams.webhooks[1].url.get_secret_value() == OTHER_URL


def test_webhook_url_is_masked_in_repr() -> None:
    """SecretStr keeps the URL (which embeds a token) out of repr/log output."""
    s = _settings({"channel": "alerts", "url": URL})
    assert URL not in repr(s.teams)
    assert s.teams.webhooks[0].url.get_secret_value() == URL


def test_unresolved_secret_fails_fast() -> None:
    """A never-resolved '<% secrets.x %>' literal normalizes to unset, so the required field fails."""
    with pytest.raises(ValidationError):
        _settings({"channel": "alerts", "url": "<% secrets.teams-webhook-alerts %>"})


def test_one_unresolved_secret_fails_the_whole_config() -> None:
    """A half-wired deployment is fatal: a good entry doesn't excuse an un-wired one."""
    with pytest.raises(ValidationError):
        _settings(
            {"channel": "alerts", "url": URL},
            {"channel": "ops", "url": "<% secrets.teams-webhook-ops %>"},
        )


def test_blank_webhook_url_fails() -> None:
    """A blank webhook URL fails validation instead of deferring the failure to the first send."""
    with pytest.raises(ValidationError):
        _settings({"channel": "alerts", "url": ""})


def test_missing_webhook_url_fails() -> None:
    """The webhook URL is required."""
    with pytest.raises(ValidationError):
        _settings({"channel": "alerts"})


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com/hook", "example.com/hook"])
def test_rejects_non_http_url(url: str) -> None:
    """The webhook must be an http(s) URL, so a typo fails at config time, not on first send."""
    with pytest.raises(ValidationError):
        _settings({"channel": "alerts", "url": url})


def test_url_validation_error_does_not_leak_the_url() -> None:
    """The rejection reason carries no part of the URL, which embeds a token.

    This pins the shape main.py actually logs (`include_input=False`). Pydantic records the raw
    input on the error regardless, so `str(exc)` does contain the URL -- which is exactly why the
    app must never report a configuration error that way.
    """
    with pytest.raises(ValidationError) as excinfo:
        _settings({"channel": "alerts", "url": "ftp://example.com/hook?token=SUPERSECRETVALUE"})
    logged = str(excinfo.value.errors(include_url=False, include_input=False))
    assert "SUPERSECRETVALUE" not in logged
    assert "url must be a valid http(s) URL" in logged


def test_blank_channel_fails() -> None:
    """A blank label can't be selected by any payload, so the entry would be unreachable."""
    with pytest.raises(ValidationError):
        _settings({"channel": "   ", "url": URL})


def test_channel_is_stripped() -> None:
    """Surrounding whitespace in the configured label is dropped, not preserved."""
    s = _settings({"channel": "  alerts  ", "url": URL})
    assert s.teams.webhooks[0].channel == "alerts"


@pytest.mark.parametrize("second", ["alerts", "ALERTS", " Alerts "])
def test_duplicate_channels_fail(second: str) -> None:
    """Two entries sharing a label (ignoring case/whitespace) would route half the sends nowhere visible."""
    with pytest.raises(ValidationError, match="duplicate channel"):
        _settings({"channel": "alerts", "url": URL}, {"channel": second, "url": OTHER_URL})


def test_empty_webhook_list_fails() -> None:
    """An empty list satisfies 'webhooks is required' but leaves every action with nowhere to go."""
    with pytest.raises(ValidationError):
        _settings()


def test_missing_teams_block_fails() -> None:
    """The teams provider block is required."""
    with pytest.raises(ValidationError):
        Settings()


def test_ignores_unknown_top_level_keys() -> None:
    """Platform-injected keys outside the model are ignored, not fatal."""
    s = _settings({"channel": "alerts", "url": URL}, some_platform_key="x")
    assert s.teams.webhooks[0].url.get_secret_value() == URL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("alerts", "alerts"), ("  Alerts ", "alerts"), ("OPS", "ops")],
)
def test_normalize_channel(raw: str, expected: str) -> None:
    """Lookup keys are stripped and case-folded, so an operator's casing doesn't decide delivery."""
    assert normalize_channel(raw) == expected
