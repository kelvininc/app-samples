import asyncio
from asyncio import Queue
from datetime import timedelta

from kelvin.app import KelvinApp, ControlChange, filters
from kelvin.message import Number
from kelvin.message.krn import KRNAssetDataStream


async def process_motor_temperature_change(app: KelvinApp, motor_temperature_msg: Number):
    if motor_temperature_msg.payload > 75:
        motor_speed_value = 1000

        # Build & Publish Control Change
        await app.publish(
                ControlChange(
                    expiration_date=timedelta(minutes=5),
                    payload=motor_speed_value,
                    resource=KRNAssetDataStream(motor_temperature_msg.resource.asset, "motor_speed_set_point")
                )
            )

async def main() -> None:
    app = KelvinApp()
    await app.connect()

    # Create a Filtered Queue with Temperature (Number) Input Messages
    motor_temperature_msg_queue: Queue[Number] = app.filter(filters.input_equals("motor_temperature"))

    while True:
        # Wait & Read new Temperature Inputs
        motor_temperature_msg = await motor_temperature_msg_queue.get()

        print("Receive Motor Temperature: ", motor_temperature_msg)

        # Process the Temperature change
        await process_motor_temperature_change(app, motor_temperature_msg)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
