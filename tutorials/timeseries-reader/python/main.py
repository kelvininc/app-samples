import structlog
from kelvin.application import KelvinApp
from kelvin.message import AssetDataMessage

app = KelvinApp()
logger = structlog.get_logger()


@app.stream(inputs=["temperature_c"])
async def receive_temperature(message: AssetDataMessage) -> None:
    """Log each selected temperature with its source Asset and Data Stream."""
    logger.info(
        "motor_temperature_received",
        asset=message.resource.asset,
        data_stream=message.resource.data_stream,
        value=message.payload,
    )


if __name__ == "__main__":
    app.run()
