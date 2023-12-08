import asyncio
from datetime import timedelta

import pandas as pd
from kelvin.application import KelvinApp, filters
from kelvin.message import Number
from rolling_window import RollingWindow

WINDOW_TIME = 5 * 60  # 5 minutes


async def run_calculations(asset: str, df: pd.DataFrame) -> None:
    # Print data frame
    print(f"# Asset: {asset}")
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
        max_duration=WINDOW_TIME, round_to=timedelta(seconds=1)
    )

    while True:
        # Await a new message from the queue
        message = await msg_queue.get()

        # Add the message to the rolling window
        rolling_window.add_message(message)

        # Retrieve all the dataframes from the rolling window
        dataframes = rolling_window.get_dataframes()
        for asset, df in dataframes.items():
            # Run the calculations for each asset DataFrame
            await run_calculations(asset, df)


if __name__ == "__main__":
    asyncio.run(main())
