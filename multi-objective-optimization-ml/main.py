import asyncio
import logging
from datetime import timedelta

import pandas as pd
from kelvin.application import KelvinApp, filters
from kelvin.message import ControlChange, Number, Recommendation
from kelvin.message.krn import KRNAsset, KRNAssetDataStream
from multi_objective_optimization import run_model
from rolling_window import RollingWindow

# Configure logging
logging.basicConfig(level=logging.INFO)


async def process_data(app: KelvinApp, asset: str, df: pd.DataFrame) -> None:
    try:
        # Run model
        recommended_setpoints = run_model(df)

        if recommended_setpoints:
            # Create a control change for each recommended set point
            control_changes = []
            for setpoint_name, value in recommended_setpoints.items():
                control_change = ControlChange(
                    resource=KRNAssetDataStream(asset=asset, data_stream=setpoint_name),
                    payload=value,
                    expiration_date=timedelta(hours=1),
                )
                control_changes.append(control_change)

            # Create and Publish a Recommendation with all control changes
            await app.publish(
                Recommendation(
                    resource=KRNAsset(asset=asset),
                    type="multi_objective_optimization",
                    description="Multi Objective Optimization",
                    control_changes=control_changes,
                )
            )
    except Exception as e:
        logging.error(f"Error processing data for asset {asset}: {e}")


async def main() -> None:
    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    # Subscribe to the asset data streams
    msg_queue: asyncio.Queue[Number] = app.filter(filters.is_asset_data_message)

    # Create a rolling window
    rolling_window = RollingWindow(
        max_data_points=500, timestamp_rounding_interval=timedelta(seconds=1)
    )

    while True:
        # Await a new message from the queue
        message = await msg_queue.get()

        # Add the message to the rolling window
        rolling_window.add_message(message)

        # Get asset
        asset = message.resource.asset

        # Retrieve dataframe from the rolling window for the specified asset
        df = rolling_window.get_asset_dataframe(asset)

        # Process the data
        await process_data(app, asset, df)


if __name__ == "__main__":
    asyncio.run(main())
