import asyncio
from asyncio import Queue
from datetime import timedelta

from kelvin.application import KelvinApp, filters
from kelvin.message import Number, ControlChange, Recommendation
from kelvin.message.krn import KRNAssetDataStream, KRNAsset


async def process_motor_temperature_change(app: KelvinApp, asset, value):
    # Get App Parameter
    temperature_threshold_tolerance = app.app_parameters["temperature_threshold_tolerance"]

    # Get Asset Parameter
    temperature_max_threshold = app.asset_parameters[asset]["temperature_max_threshold"]
    
    if value > temperature_max_threshold + temperature_threshold_tolerance:
        speed_decrease_set_point_value = app.asset_parameters[asset]["speed_decrease_set_point"]

        # Build Control Change
        control_change = ControlChange(
            resource=KRNAssetDataStream(asset, "motor_speed_set_point"),
            payload=speed_decrease_set_point_value,
            expiration_date=timedelta(minutes=5)
        )

        if app.asset_parameters[asset]["closed_loop"]:
            # Publish Control Change
            await app.publish(control_change)

            print(f"\nPublished Motor Speed SetPoint Control Change: {speed_decrease_set_point_value}")            
        else:
            # Build and Publish Recommendation
            await app.publish(
                Recommendation(
                    resource=KRNAsset(asset),
                    type="decrease_speed",
                    control_changes=[control_change]
                )
            )

            print(f"\nPublished Motor Speed SetPoint (Control Change) Recommendation: {speed_decrease_set_point_value}")            

async def convert_temperature_to_fahrenheit(app: KelvinApp, asset, value):
    motor_temperature_fahrenheit_value = (1.8 * value) + 32

    await app.publish(
        Number(resource=KRNAssetDataStream(asset, "motor_temperature_fahrenheit"), payload=motor_temperature_fahrenheit_value)
    )

async def main() -> None:
    app = KelvinApp()
    await app.connect()

    print("Application Parameters: ", app.app_parameters)
    print("Asset Parameters: ", app.asset_parameters)

    # Create a Filtered Queue with Temperature (Number) Input Messages
    motor_temperature_msg_queue: Queue[Number] = app.filter(filters.input_equals("motor_temperature"))

    while True:
        # Wait & Read new Temperature Inputs
        motor_temperature_msg = await motor_temperature_msg_queue.get()

        asset = motor_temperature_msg.resource.asset
        value = motor_temperature_msg.payload

        print(f"\nReceived Motor Temperature | Asset: {asset} | Value: {value}")

        # Process the Temperature change
        await process_motor_temperature_change(app, asset, value)

        # Publish Temperature in Fahrenheit
        await convert_temperature_to_fahrenheit(app, asset, value)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
