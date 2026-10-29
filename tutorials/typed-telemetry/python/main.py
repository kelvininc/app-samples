from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.message import (
    AssetDataMessage,
    Boolean,
    KMessageTypeData,
    Message,
    Number,
    String,
)

app = KelvinApp()


@app.stream(inputs=["temperature_c"])
async def publish_telemetry(message: AssetDataMessage) -> None:
    """Publish four typed outputs from the same temperature reading."""
    asset = message.resource.asset
    fahrenheit = message.payload * 9 / 5 + 32
    high = message.payload > 80
    await app.publish(
        Number(
            resource=KRNAssetDataStream(asset, "temperature_f"),
            payload=fahrenheit,
            timestamp=message.timestamp,
        )
    )
    await app.publish(
        Boolean(
            resource=KRNAssetDataStream(asset, "temperature_alarm"),
            payload=high,
            timestamp=message.timestamp,
        )
    )
    await app.publish(
        String(
            resource=KRNAssetDataStream(asset, "temperature_status"),
            payload="high" if high else "normal",
            timestamp=message.timestamp,
        )
    )
    await app.publish(
        Message(
            type=KMessageTypeData("object"),
            resource=KRNAssetDataStream(asset, "temperature_summary"),
            payload={"celsius": message.payload, "fahrenheit": fahrenheit},
            timestamp=message.timestamp,
        )
    )


if __name__ == "__main__":
    app.run()
