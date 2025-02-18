import asyncio
from typing import AsyncGenerator
from datetime import timedelta

from kelvin.application import KelvinApp, filters
from kelvin.message import Number, ControlChange, Recommendation
from kelvin.krn import KRNAssetDataStream, KRNAsset
         

async def main() -> None:
    app = KelvinApp()
    await app.connect()

    # Create a Filtered Stream with Temperature (Number) Input Messages
    motor_temperature_msg_stream: AsyncGenerator[Number, None] = app.stream_filter(filters.input_equals("motor_temperature"))

    # Wait & Read new Temperature Inputs
    async for motor_temperature_msg in motor_temperature_msg_stream:
        asset = motor_temperature_msg.resource.asset
        value = motor_temperature_msg.payload

        print(f"\nReceived Motor Temperature | Asset: {asset} | Value: {value}")

        # Check if the Temperature is above the Max Threshold
        if value > app.assets[asset].parameters["temperature_max_threshold"]:

            # Build Control Change Object
            control_change = ControlChange(
                resource=KRNAssetDataStream(asset, "motor_speed_set_point"),
                payload=app.assets[asset].parameters["speed_decrease_set_point"],
                expiration_date=timedelta(minutes=10)
            )

            if app.assets[asset].parameters["closed_loop"]:
                # Publish Control Change
                await app.publish(control_change)

                print(f"\nPublished Motor Speed SetPoint Control Change: {control_change.payload}")            
            else:
                # Build and Publish Recommendation
                await app.publish(
                    Recommendation(
                        resource=KRNAsset(asset),
                        type="decrease_speed",
                        control_changes=[control_change]
                    )
                )

                print(f"\nPublished Motor Speed SetPoint (Control Change) Recommendation: {control_change.payload}") 

                
if __name__ == "__main__":
    asyncio.run(main())
