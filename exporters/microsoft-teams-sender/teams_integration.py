import asyncio

import aiohttp
from kelvin.logs import logger
from pydantic import BaseModel
from settings import Settings


class TeamsIntegrationResponse(BaseModel):
    success: bool
    message: str


class TeamsIntegration:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send_teams_message(self, channel_name: str, message: str) -> TeamsIntegrationResponse:
        webhook = self.settings.webhook_for(channel_name)

        if not webhook:
            logger.error("No webhook configured for channel", channel=channel_name)

            return TeamsIntegrationResponse(
                success=False, message=f"No webhook configured for Teams channel '{channel_name}'"
            )

        logger.info("Sending Teams message", channel=channel_name, message=message)

        try:
            status, body = await self._post(webhook.url.get_secret_value(), self.build_card(message))
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            logger.error(
                "Failed to send the Teams message",
                channel=channel_name,
                error=str(e),
                error_type=type(e).__name__,
            )

            return TeamsIntegrationResponse(
                success=False, message=f"Failed to send the Teams message ({type(e).__name__})"
            )

        # Power Automate workflows reply 202; classic incoming webhooks reply 200.
        if status >= 300:
            logger.error("Teams webhook rejected the message", channel=channel_name, status=status, body=body[:200])

            return TeamsIntegrationResponse(
                success=False, message=f"Teams webhook rejected the message (HTTP {status})"
            )

        logger.info("Teams message sent", channel=channel_name, status=status)

        return TeamsIntegrationResponse(success=True, message="Teams message sent")

    async def _post(self, url: str, payload: dict) -> tuple[int, str]:
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                return response.status, await response.text()

    @staticmethod
    def build_card(text: str) -> dict:
        """Wrap the text in the Adaptive Card envelope Teams webhooks expect."""
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": text,
                                "wrap": True,
                            }
                        ],
                    },
                }
            ],
        }
