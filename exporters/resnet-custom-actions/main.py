import asyncio

from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import CustomAction
from resnet import ResnetIntegration


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
        if action.type.lower() != "resnet issue":
            logger.warning("Received unexpected Custom Action", action=action)
            return

        logger.info("Received Resnet Action", action=action)

        # Call integration
        config = self.app.app_configuration
        integration = ResnetIntegration(url=config["url"], tenant=config["tenant"], api_key=config["api_key"])
        response = await integration.create_issue(payload=action.payload)

        # Publish action result
        result = action.result(success=response.success, message=response.message, metadata=response.metadata)
        await self.app.publish(result)

        logger.info("Finished handling Resnet Action", action=action, success=result.success)

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
