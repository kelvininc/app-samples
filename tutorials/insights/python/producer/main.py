from kelvin.application import KelvinApp
from kelvin.krn import KRNAsset, KRNDatastream
from kelvin.message import AssetDataMessage, Insight

app = KelvinApp()


@app.stream(inputs=["temperature_c"])
async def mark_high_temperature(message: AssetDataMessage) -> None:
    """Label each above-threshold reading with a point Insight."""
    if message.payload <= 80:
        return
    await app.publish(
        Insight(
            resource=KRNAsset(message.resource.asset),
            tag_name="temperature_high",
            start_date=message.timestamp,  # Label the reading's time.
            description="Motor temperature exceeded 80 degrees Celsius.",
            contexts=[KRNDatastream("temperature_c")],
        )
    )


if __name__ == "__main__":
    app.run()
