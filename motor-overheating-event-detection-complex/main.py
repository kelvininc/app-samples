import asyncio
from asyncio import Queue
from datetime import timedelta

from kelvin.app import KelvinApp, ControlChange, Recommendation, filters
from kelvin.message import Number
from kelvin.message.krn import KRNAssetDataStream, KRNAsset


async def process_motor_temperature_change(app: KelvinApp, asset, value):
    if value > app.asset_parameters[asset]["temperature_max_threshold"] + app.app_parameters["temperature_threshold_tolerance"]:
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
        else:
            # Build and Publish Control Change
            await app.publish(
                Recommendation(
                    resource=KRNAsset(asset),
                    type="decrease_speed",
                    control_changes=[control_change]
                )
            )

async def convert_temperature_to_fahrenheit(app: KelvinApp, asset, value):
    motor_temperature_fahrenheit_value = (1.8 * value) + 32

    await app.publish(
        Number(resource=KRNAssetDataStream(asset, "motor_temperature_fahrenheit"), payload=motor_temperature_fahrenheit_value)
    )

async def main() -> None:
    app = KelvinApp()
    await app.connect()

    # Create a Filtered Queue with Temperature (Number) Input Messages
    motor_temperature_msg_queue: Queue[Number] = app.filter(filters.input_equals("motor_temperature"))

    while True:
        # Wait & Read new Temperature Inputs
        motor_temperature_msg = await motor_temperature_msg_queue.get()

        print("Received Motor Temperature: ", motor_temperature_msg)

        asset = motor_temperature_msg.resource.asset
        value = motor_temperature_msg.payload

        # Process the Temperature change
        await process_motor_temperature_change(app, asset, value)

        # Publish Temperature in Fahrenheit
        await convert_temperature_to_fahrenheit(app, asset, value)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
