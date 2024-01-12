import asyncio
from datetime import timedelta

import pandas as pd
from kelvin.application import KelvinApp, filters
from kelvin.message import Number
from rolling_window import RollingWindow


async def run_calculations(asset: str, df: pd.DataFrame) -> None:
    # Print data frame
    print(f"\n### Asset: {asset}")
    print(df)


async def main() -> None:
    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    # Subscribe to the asset data streams
    msg_queue: asyncio.Queue[Number] = app.filter(filters.is_asset_data_message)

    # Create a rolling window
    rolling_window = RollingWindow(
        max_window_duration=300,  # max of 5 minutes of data
        max_data_points=10,  # max of 10 data points
        timestamp_rounding_interval=timedelta(seconds=1),  # round to the nearest second
    )

    while True:
        # Await a new message from the queue
        message = await msg_queue.get()

        # Add the message to the rolling window
        rolling_window.add_message(message)

        # Retrieve all the dataframes from the rolling window
        dataframes = rolling_window.get_all_asset_dataframes()
        for asset, df in dataframes.items():
            # Run the calculations for each asset DataFrame
            await run_calculations(asset, df)


if __name__ == "__main__":
    asyncio.run(main())
