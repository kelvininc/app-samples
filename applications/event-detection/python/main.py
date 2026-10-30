"""Publish converted temperatures and recommend lower motor speeds when hot."""

from datetime import timedelta

from kelvin.application import KelvinApp
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message import AssetDataMessage, ControlChange, Number, Recommendation
from kelvin.message.evidences import Markdown

app = KelvinApp()


@app.stream(inputs=["temperature_c"])
async def on_motor_temperature(message: AssetDataMessage) -> None:
    """Preserve telemetry identity and use the source Asset's settings."""
    asset = message.resource.asset
    await app.publish(
        Number(
            resource=KRNAssetDataStream(asset, "temperature_f"),
            payload=message.payload * 9 / 5 + 32,
            timestamp=message.timestamp,
        )
    )
    parameters = app.assets[asset].parameters
    threshold = parameters["temperature_max_threshold"]
    if message.payload <= threshold:
        return
    await app.publish(
        Recommendation(
            resource=KRNAsset(asset),
            type="decrease_speed",
            expiration_date=app.clock.now() + timedelta(minutes=5),
            auto_accepted=parameters["kelvin_closed_loop"],
            control_changes=[
                ControlChange(
                    resource=KRNAssetDataStream(asset, "motor_speed_setpoint"),
                    payload=parameters["speed_decrease_set_point"],
                    retries=0,
                )
            ],
            metadata={"temperature_c": message.payload},
            evidences=[
                Markdown(
                    title="High motor temperature",
                    markdown=f"Temperature is {message.payload} C; the limit is {threshold} C.",
                )
            ],
        )
    )


if __name__ == "__main__":
    app.run()
