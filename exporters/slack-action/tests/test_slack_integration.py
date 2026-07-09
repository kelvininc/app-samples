"""Unit tests for SlackIntegration against a faked Slack web client (no network)."""
import aiohttp
import pytest
from slack_sdk.errors import SlackApiError

import slack_integration as si
from settings import Slack
from slack_integration import SlackIntegration, SlackSendError

pytestmark = pytest.mark.asyncio


class _FakeClient:
    """Stands in for slack_sdk AsyncWebClient; canned channel list + recorded posts/joins."""

    def __init__(
        self,
        channels: tuple[tuple[str, str], ...] = (("general", "C1"), ("alerts", "C2")),
        list_error: str | None = None,
    ) -> None:
        self._channels = [{"name": n, "id": i} for n, i in channels]
        self.list_error = list_error
        self.list_calls = 0
        # Queue of failures for successive chat_postMessage calls: a str raises
        # SlackApiError with that error code, an Exception instance is raised as-is.
        self.post_errors: list[str | Exception] = []
        self.posted: list[tuple] = []
        self.joined: list[str] = []

    async def conversations_list(self, exclude_archived, limit, cursor):
        self.list_calls += 1
        if self.list_error:
            raise SlackApiError("err", {"error": self.list_error})
        return {"channels": self._channels, "response_metadata": {"next_cursor": ""}}

    async def conversations_join(self, channel):
        self.joined.append(channel)

    async def chat_postMessage(self, channel, text):
        if self.post_errors:
            error = self.post_errors.pop(0)
            if isinstance(error, Exception):
                raise error
            raise SlackApiError("err", {"error": error})
        self.posted.append((channel, text))
        return {"ok": True}


def _integration(monkeypatch: pytest.MonkeyPatch, **client_kwargs) -> tuple[SlackIntegration, _FakeClient]:
    client = _FakeClient(**client_kwargs)
    monkeypatch.setattr(si, "AsyncWebClient", lambda token, timeout: client)
    return SlackIntegration(Slack(token="xoxb-test")), client


async def test_sends_message_to_resolved_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known channel name is resolved to its id and posted to without joining first."""
    integration, client = _integration(monkeypatch)
    await integration.send_message("alerts", "hello")
    assert client.posted == [("C2", "hello")]
    assert client.joined == []           # post-first: no join when already a member


async def test_cache_hit_skips_second_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second send to the same channel reuses the cached id (no second conversations_list)."""
    integration, client = _integration(monkeypatch)
    await integration.send_message("alerts", "one")
    await integration.send_message("alerts", "two")
    assert client.list_calls == 1
    assert client.posted == [("C2", "one"), ("C2", "two")]


async def test_unknown_channel_raises_without_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown channel name raises SlackSendError and posts nothing."""
    integration, client = _integration(monkeypatch)
    with pytest.raises(SlackSendError, match="'nope' not found"):
        await integration.send_message("nope", "hello")
    assert client.posted == []


async def test_lookup_failure_surfaces_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An API error during lookup surfaces its error code instead of 'channel not found'."""
    integration, client = _integration(monkeypatch, list_error="invalid_auth")
    with pytest.raises(SlackSendError, match=r"lookup failed \(invalid_auth\)"):
        await integration.send_message("alerts", "hello")
    assert client.posted == []


async def test_not_in_channel_triggers_join_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A not_in_channel post error joins the channel and retries the post once."""
    integration, client = _integration(monkeypatch)
    client.post_errors = ["not_in_channel"]
    await integration.send_message("alerts", "hello")
    assert client.joined == ["C2"]
    assert client.posted == [("C2", "hello")]


async def test_other_post_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-recoverable post error raises SlackSendError with the Slack error code."""
    integration, client = _integration(monkeypatch)
    client.post_errors = ["msg_too_long"]
    with pytest.raises(SlackSendError, match=r"Slack API error \(msg_too_long\)"):
        await integration.send_message("alerts", "hello")
    assert client.joined == []


async def test_channel_not_found_pops_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel_not_found post error evicts the stale cache entry, forcing a fresh lookup."""
    integration, client = _integration(monkeypatch)
    await integration.send_message("alerts", "one")       # caches alerts -> C2
    assert client.list_calls == 1

    client.post_errors = ["channel_not_found"]
    with pytest.raises(SlackSendError, match="'alerts' no longer exists"):
        await integration.send_message("alerts", "two")

    await integration.send_message("alerts", "three")     # cache miss -> fresh lookup
    assert client.list_calls == 2


async def test_network_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network-level failure is wrapped in SlackSendError."""
    integration, client = _integration(monkeypatch)
    client.post_errors = [aiohttp.ClientError("connection reset")]
    with pytest.raises(SlackSendError, match="Slack request failed"):
        await integration.send_message("alerts", "hello")


async def test_token_and_timeout_passed_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SecretStr token is unwrapped and the 10s timeout set when building the client."""
    seen: list[tuple] = []
    monkeypatch.setattr(si, "AsyncWebClient", lambda token, timeout: seen.append((token, timeout)) or _FakeClient())
    SlackIntegration(Slack(token="xoxb-unwrap"))
    assert seen == [("xoxb-unwrap", 10)]
