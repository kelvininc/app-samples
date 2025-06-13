import asyncio

from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import CustomAction
from slack_integration import SlackIntegration


class ExporterApplication:
    def __init__(self, app: KelvinApp = KelvinApp()) -> None:
        self.app = app
        self.app.on_connect = self.on_connect
        self.app.on_disconnect = self.on_disconnect
        self.app.on_app_configuration = self.on_app_configuration
        self.app.on_custom_action = self.on_custom_action

    async def on_connect(self) -> None:
        logger.info("App connected.")

    async def on_disconnect(self) -> None:
        logger.info("App disconnected.")

    async def on_app_configuration(self, config: dict) -> None:
        logger.info("App configuration changed.", config=config)

    async def on_custom_action(self, action: CustomAction):
        if action.type.lower() != "slack message":
            logger.warning("Received unexpected Custom Action", action=action)
            return

        logger.info("Received Slack Message Action", action=action)

        channel = action.payload.get("channel")
        if not channel:
            logger.error("Channel is not specified in the action payload", action=action)

            result = action.result(success=False, message="Channel is not specified")
            await self.app.publish(result)

            return

        message = action.payload.get("message", {}).get("text")
        if not message:
            logger.error("Message is not specified in the action payload", action=action)

            result = action.result(success=False, message="Message is not specified")
            await self.app.publish(result)

            return

        # Call integration
        integration = SlackIntegration(token=self.app.app_configuration["token"])

        response = await integration.send_slack_message(channel_name=channel, message=message)

        # Publish action result
        result = action.result(success=response.success, message=response.message)
        await self.app.publish(result)

        logger.info("Finished handling Slack Message Action", action=action, success=result.success)

    async def main(self) -> None:
        try:
            # Connect to Kelvin
            await self.app.connect()

            # Sleep "forever” in a clean, coroutine-friendly way:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger.warning("Received CancelledError. Disconnecting...")
        finally:
            await self.app.disconnect()


if __name__ == "__main__":
    app = ExporterApplication()
    asyncio.run(app.main())
