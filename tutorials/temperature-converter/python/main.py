"""Convert each motor temperature from Celsius to Fahrenheit."""

from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.message import AssetDataMessage, Number

app = KelvinApp()


@app.stream(inputs=["temperature_c"])
async def convert_temperature(message: AssetDataMessage) -> None:
    """Preserve the Asset and timestamp when publishing the converted reading."""
    await app.publish(
        Number(
            resource=KRNAssetDataStream(message.resource.asset, "temperature_f"),
            payload=message.payload * 9 / 5 + 32,
            timestamp=message.timestamp,
        )
    )


if __name__ == "__main__":
    app.run()
