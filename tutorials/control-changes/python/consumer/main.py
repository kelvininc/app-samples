from kelvin.application import KelvinApp
from kelvin.message import AssetDataMessage, ControlAck, StateEnum

app = KelvinApp()


@app.on_control_change
async def simulate_speed_request(message: AssetDataMessage) -> None:
    """Acknowledge a simulated command without writing to equipment."""
    accepted = 0 <= message.payload <= 1500
    await app.publish(
        ControlAck(
            resource=message.resource,
            state=StateEnum.applied if accepted else StateEnum.rejected,
            message="Simulated speed accepted"
            if accepted
            else "Speed must be between 0 and 1500",
        )
    )


if __name__ == "__main__":
    app.run()
