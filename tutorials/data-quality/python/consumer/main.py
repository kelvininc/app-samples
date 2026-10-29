import structlog
from kelvin.application import KelvinApp, filters
from kelvin.krn import KRNAssetDataStreamDataQuality

app = KelvinApp()
logger = structlog.get_logger()


@app.task
async def receive_quality() -> None:
    """Consume quality resources through the shared message filter."""
    async for message in app.stream_filter(filters.is_data_quality_message):
        resource = message.resource
        stream = (
            resource.data_stream
            if isinstance(resource, KRNAssetDataStreamDataQuality)
            else None
        )
        logger.info(
            "motor_quality_received",
            asset=resource.asset,
            metric=resource.data_quality,
            data_stream=stream,
            value=message.payload,
        )


if __name__ == "__main__":
    app.run()
