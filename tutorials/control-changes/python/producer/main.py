from datetime import timedelta

from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.message import AssetDataMessage, ControlChange

app = KelvinApp()


@app.stream(inputs=["temperature_c"])
async def request_lower_speed(message: AssetDataMessage) -> None:
    """Request a lower setpoint for an above-threshold motor reading."""
    if message.payload <= 80:
        return
    await app.publish(
        ControlChange(
            resource=KRNAssetDataStream(message.resource.asset, "motor_speed_setpoint"),
            payload=1000,
            expiration_date=app.clock.now() + timedelta(minutes=5),
            retries=0,  # No additional write requests after the initial attempt.
            retry_interval=60,  # Seconds between retries if retries are enabled.
        )
    )


if __name__ == "__main__":
    app.run()
