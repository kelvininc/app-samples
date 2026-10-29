from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStreamDataQuality
from kelvin.message import AssetDataMessage, Number

app = KelvinApp()


@app.stream(inputs=["temperature_c"])
async def assess_temperature(message: AssetDataMessage) -> None:
    """Score the example's inclusive operating range as 100 or 0."""
    in_range = 0 <= message.payload <= 80
    await app.publish(
        Number(
            resource=KRNAssetDataStreamDataQuality(
                message.resource.asset, "temperature_c", "temperature_range"
            ),
            payload=100 if in_range else 0,
            timestamp=message.timestamp,
        )
    )


if __name__ == "__main__":
    app.run()
