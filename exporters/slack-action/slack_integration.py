import asyncio
from typing import Optional

import aiohttp
from kelvin.logs import logger
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from settings import Slack


class SlackSendError(Exception):
    """A Slack send failed; the message is operator-readable and goes into the failure ack."""


class SlackIntegration:
    """Posts messages to public Slack channels, resolving channel names to IDs on demand.

    Built once at connect and reused across actions: it holds a single AsyncWebClient and a
    per-name cache of the channel IDs this app has resolved (not a full workspace map).
    """

    def __init__(self, config: Slack) -> None:
        self.client = AsyncWebClient(token=config.token.get_secret_value(), timeout=10)
        self._channels: dict[str, str] = {}

    async def _lookup(self, name: str) -> Optional[str]:
        """Scan the workspace's public channels for `name`, returning its ID or None.

        Paginates conversations_list and early-exits on the first match.

        Raises:
            SlackSendError: If the Slack API rejects the lookup (e.g. invalid_auth), so auth
                errors aren't misreported as channel-not-found.
        """
        cursor = None
        try:
            while True:
                response = await self.client.conversations_list(exclude_archived=True, limit=1000, cursor=cursor)

                for channel in response["channels"] or []:      # the field is Optional in the SDK
                    if channel["name"] == name:
                        return channel["id"]

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    return None
        except SlackApiError as e:
            raise SlackSendError(f"Slack channel lookup failed ({e.response['error']})") from e

    async def _resolve(self, name: str) -> str:
        """Return the channel ID for `name`, from cache or a fresh lookup.

        Raises:
            SlackSendError: If the channel doesn't exist or the lookup fails.
        """
        channel_id = self._channels.get(name)
        if channel_id is not None:
            return channel_id

        channel_id = await self._lookup(name)
        if channel_id is None:
            raise SlackSendError(f"Slack channel '{name}' not found")

        self._channels[name] = channel_id
        return channel_id

    async def send_message(self, channel_name: str, message: str) -> None:
        """Post `message` to the public channel `channel_name`, joining it on demand.

        Posts first and only joins (then retries once) when Slack answers not_in_channel,
        so the common already-a-member case costs a single API call.

        Raises:
            SlackSendError: On any failure; unknown channel, Slack API error, or a
                network-level error (the message is operator-readable).
        """
        try:
            channel_id = await self._resolve(channel_name)

            logger.info("Sending Slack message", channel=channel_id, message=message)
            try:
                try:
                    await self.client.chat_postMessage(channel=channel_id, text=message)
                except SlackApiError as e:
                    if e.response["error"] != "not_in_channel":
                        raise
                    await self.client.conversations_join(channel=channel_id)
                    await self.client.chat_postMessage(channel=channel_id, text=message)
            except SlackApiError as e:
                error = e.response["error"]
                if error == "channel_not_found":
                    self._channels.pop(channel_name, None)   # stale cache entry
                    raise SlackSendError(f"Slack channel '{channel_name}' no longer exists") from e
                raise SlackSendError(f"Slack API error ({error})") from e
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            # Network-level failures (covers both the lookup and the post).
            raise SlackSendError(f"Slack request failed ({e})") from e
