from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from kelvin.logs import logger
from pydantic import BaseModel


class SlackIntegrationResponse(BaseModel):
    success: bool
    message: str


class SlackIntegration:

    def __init__(self, token: str) -> None:

        if token is None:
            raise ValueError("token is not set in the app configuration")

        self.token = token

    async def send_slack_message(self, channel_name: str, message: str) -> SlackIntegrationResponse:
        client = AsyncWebClient(token=self.token)
        
        channel = await self.get_channel_id_by_name(channel_name)

        if not channel:
            logger.error(f"Channel not found", channel=channel_name)

            return SlackIntegrationResponse(success=False, message=f"Slack channel '{channel_name}' not found")

        try:
            # Join the channel if not already a member
            await client.conversations_join(channel=channel)

            logger.info("Sending Slack message", channel=channel, message=message)
            
            # Send the message to the specified Slack channel
            response = await client.chat_postMessage(
                channel=channel,
                text=message
            )

            logger.info("Slack message sent", response=response)
            
            return SlackIntegrationResponse(success=True, message="Slack message sent")

        except SlackApiError as e:
            logger.error(f"Failed to send the Slack message", error=e.response["error"])
            
            return SlackIntegrationResponse(success=False, message=f"Failed to send the Slack message ({e.response["error"]})")

    async def get_channel_id_by_name(self, name: str) -> None:
        client = AsyncWebClient(token=self.token)
        cursor = None

        try:
            while True:
                response = await client.conversations_list(
                    exclude_archived=True,
                    limit=1000,
                    cursor=cursor
                )

                for channel in response["channels"]:
                    if channel["name"] == name:
                        return channel["id"]

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        except SlackApiError as e:
            logger.error("Slack API error", error=e.response["error"])
        except Exception as e:
            logger.error("Unexpected error", exception=e)

        return None