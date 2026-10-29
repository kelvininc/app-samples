"""Consume selected high-temperature messages in an SDK-managed task."""

from typing import TypeGuard

from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Boolean, Message, Number

app = KelvinApp()


def motor_overheated(message: Message) -> TypeGuard[Number]:
    """Select numeric Motor 01 temperature readings above 80 degrees Celsius."""
    return (
        isinstance(message, Number)
        and isinstance(message.resource, KRNAssetDataStream)
        and message.resource == KRNAssetDataStream("motor-01", "temperature_c")
        and message.payload > 80
    )


@app.task
async def consume_hot_readings() -> None:
    """Publish an alert for each selected reading until the SDK stops the task."""
    async for message in app.stream_filter(motor_overheated):
        await app.publish(
            Boolean(
                resource=KRNAssetDataStream(
                    message.resource.asset, "temperature_alert"
                ),
                payload=True,
                timestamp=message.timestamp,
            )
        )


if __name__ == "__main__":
    app.run()
