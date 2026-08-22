import asyncio
from typing import Optional

import aiohttp
from kelvin.logs import logger

from settings import Teams

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
    """Posts Adaptive Cards to a Microsoft Teams Incoming Webhook (Power Automate Workflows).

    There's no channel selection; the webhook URL is bound to one channel.

    One instance holds one pooled `aiohttp.ClientSession`; build it in `on_connect` (a running
    event loop is required) and `close()` it in `on_disconnect`.
    """

    def __init__(self, config: Teams) -> None:
        self.config = config
        # Cap the whole request at 10s so a hung webhook fails the action instead of
        # stalling the ack (aiohttp's default total timeout is 5 minutes).
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def close(self) -> None:
        """Close the pooled HTTP session."""
        await self._session.close()

    async def send_message(self, text: str, title: Optional[str] = None) -> None:
        """POST the card to the webhook. Returns None on success; raises TeamsSendError on failure."""
        card = build_card(text, title)
        url = self.config.webhook_url.get_secret_value()
        try:
            async with self._session.post(url, json=card) as resp:
                if resp.status >= 400:                       # Workflows returns 202 Accepted on success
                    detail = (await resp.text())[:200]
                    logger.error("Teams webhook rejected the message", status=resp.status, body=detail)
                    raise TeamsSendError(f"Teams webhook returned {resp.status}")
                logger.info("Teams message sent", status=resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            # asyncio.TimeoutError (total-timeout expiry) is NOT an aiohttp.ClientError;
            # without this clause a slow webhook would escape the handler entirely.
            # Never interpolate the exception or URL: some aiohttp errors (InvalidURL,
            # response-error paths) embed the full webhook URL, and the URL path carries
            # the secret token. Log the exception TYPE only; the ack gets a fixed message.
            logger.error("Failed to reach Teams webhook (network error)", error_type=type(e).__name__)
            raise TeamsSendError("Failed to reach Teams webhook (network error)") from e
