import asyncio
from typing import Optional

import aiohttp
from kelvin.logs import logger
from pydantic import SecretStr

from settings import Teams, normalize_channel

_ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


def build_card(text: str, title: Optional[str]) -> dict:
    """Build the Power Automate 'Workflows' webhook body: an Adaptive Card in a message envelope.

    (Classic O365-connector webhooks instead accept a flat MessageCard; see the README; but
    those connectors are being retired, so this targets the Workflows format new tenants get.)
    """
    body: list[dict] = []
    if title:
        body.append({"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True})
    body.append({"type": "TextBlock", "text": text, "wrap": True})
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": _ADAPTIVE_CARD_SCHEMA,
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }


class TeamsSendError(Exception):
    """An expected, operator-reportable send failure; the message goes verbatim into the failure ack."""


class TeamsIntegration:
    """Posts Adaptive Cards to Microsoft Teams Incoming Webhooks (Power Automate Workflows).

    Each webhook URL is bound to one channel by Teams, so the action's `channel` selects which
    of the configured URLs to POST to. That choice is entirely local: Teams sees only the URL,
    never the channel name, and an unknown name fails here without a request.

    One instance holds one pooled `aiohttp.ClientSession`; build it in `on_connect` (a running
    event loop is required) and `close()` it in `on_disconnect`.
    """

    def __init__(self, config: Teams) -> None:
        # Channel label -> webhook URL, resolved once at startup.
        self._webhooks: dict[str, SecretStr] = {
            normalize_channel(webhook.channel): webhook.url for webhook in config.webhooks
        }
        # Cap the whole request at 10s so a hung webhook fails the action instead of
        # stalling the ack (aiohttp's default total timeout is 5 minutes).
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def close(self) -> None:
        """Close the pooled HTTP session."""
        await self._session.close()

    async def send_message(self, channel: str, text: str, title: Optional[str] = None) -> None:
        """POST the card to `channel`'s webhook. Returns None on success; raises TeamsSendError on failure."""
        url = self._webhooks.get(normalize_channel(channel))
        if url is None:
            # The lookup is local, so an unconfigured channel fails before any network call.
            logger.error("No webhook configured for channel", channel=channel)
            raise TeamsSendError(f"No webhook configured for Teams channel '{channel}'")

        card = build_card(text, title)
        try:
            async with self._session.post(url.get_secret_value(), json=card) as resp:
                if resp.status >= 400:                       # Workflows returns 202 Accepted on success
                    detail = (await resp.text())[:200]
                    logger.error(
                        "Teams webhook rejected the message", channel=channel, status=resp.status, body=detail
                    )
                    raise TeamsSendError(f"Teams webhook returned {resp.status}")
                logger.info("Teams message sent", channel=channel, status=resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            # asyncio.TimeoutError (total-timeout expiry) is NOT an aiohttp.ClientError;
            # without this clause a slow webhook would escape the handler entirely.
            # Never interpolate the exception or URL: some aiohttp errors (InvalidURL,
            # response-error paths) embed the full webhook URL, and the URL path carries
            # the secret token. Log the exception TYPE only; the ack gets a fixed message.
            # The channel label is safe to log; it's operator-chosen, not part of the URL.
            logger.error(
                "Failed to reach Teams webhook (network error)", channel=channel, error_type=type(e).__name__
            )
            raise TeamsSendError("Failed to reach Teams webhook (network error)") from e
