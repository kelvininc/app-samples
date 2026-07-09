"""Unit tests for TeamsIntegration against a faked aiohttp session (no network)."""
import asyncio

import aiohttp
import pytest

import teams_integration as ti
from settings import Teams
from teams_integration import TeamsIntegration, TeamsSendError, build_card

URL = "https://example.webhook.office.com/webhookb2/abc/IncomingWebhook/def"


class _FakeResp:
    def __init__(self, status: int, text: str = "") -> None:
        self.status, self._text = status, text

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False

    async def text(self) -> str:
        return self._text


class _FakeSession:
    """Stand-in for the pooled aiohttp.ClientSession; records the post calls."""

    def __init__(self, status: int = 202, raise_exc: Exception | None = None) -> None:
        self.status, self.raise_exc = status, raise_exc
        self.calls: list[tuple] = []
        self.init_kwargs: dict = {}
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    def post(self, url: str, json: dict):
        self.calls.append((url, json))
        if self.raise_exc:
            raise self.raise_exc
        return _FakeResp(self.status)


def _patch(monkeypatch: pytest.MonkeyPatch, **kw) -> _FakeSession:
    session = _FakeSession(**kw)

    def _factory(**kwargs: object) -> _FakeSession:
        session.init_kwargs = kwargs
        return session

    monkeypatch.setattr(ti.aiohttp, "ClientSession", _factory)
    return session


class TestBuildCard:
    """The Adaptive Card body builder (pure)."""

    def test_includes_text_in_an_adaptive_card_envelope(self) -> None:
        card = build_card("temp high", title=None)
        att = card["attachments"][0]
        assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
        texts = [b["text"] for b in att["content"]["body"]]
        assert texts == ["temp high"]                 # no title block when title is None

    def test_prepends_title_block_when_given(self) -> None:
        body = build_card("temp high", title="Pump 3")["attachments"][0]["content"]["body"]
        assert [b["text"] for b in body] == ["Pump 3", "temp high"]
        assert body[0]["weight"] == "Bolder"


@pytest.mark.asyncio
class TestSendMessage:
    """Posting to the webhook through the pooled session."""

    async def test_success_posts_card_to_unwrapped_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 2xx response succeeds (returns None); the card is POSTed to the unwrapped webhook URL."""
        session = _patch(monkeypatch, status=202)
        result = await TeamsIntegration(Teams(webhook_url=URL)).send_message(text="hi", title="T")
        assert result is None
        url, payload = session.calls[0]
        assert url == URL and payload["attachments"][0]["content"]["body"][-1]["text"] == "hi"

    async def test_session_has_10s_total_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pooled session caps requests at 10 seconds total."""
        session = _patch(monkeypatch)
        TeamsIntegration(Teams(webhook_url=URL))
        assert session.init_kwargs["timeout"] == aiohttp.ClientTimeout(total=10)

    async def test_close_closes_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """close() shuts down the pooled session (called from on_disconnect)."""
        session = _patch(monkeypatch)
        await TeamsIntegration(Teams(webhook_url=URL)).close()
        assert session.closed is True

    async def test_non_2xx_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 4xx/5xx response raises TeamsSendError carrying the status."""
        _patch(monkeypatch, status=400)
        with pytest.raises(TeamsSendError, match="400"):
            await TeamsIntegration(Teams(webhook_url=URL)).send_message(text="hi")

    async def test_network_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An aiohttp client error is wrapped in TeamsSendError."""
        _patch(monkeypatch, raise_exc=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(TeamsSendError, match="Failed to reach"):
            await TeamsIntegration(Teams(webhook_url=URL)).send_message(text="hi")

    async def test_timeout_raises_teams_send_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """asyncio.TimeoutError is not an aiohttp.ClientError; it must still become TeamsSendError."""
        _patch(monkeypatch, raise_exc=asyncio.TimeoutError())
        with pytest.raises(TeamsSendError, match="Failed to reach"):
            await TeamsIntegration(Teams(webhook_url=URL)).send_message(text="hi")
