import asyncio
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from kelvin.application import KelvinApp, filters
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message import ControlChange, Number, Recommendation
from kelvin.message.evidences import LineChart

from multi_objective_optimization import run_model
from rolling_window import RollingWindow

# Configure logging
logging.basicConfig(level=logging.INFO)


def clip_control_values(recommended_setpoints: dict, df: pd.DataFrame) -> dict:
    """
    Clip control change values to not exceed ±10% of the last known value.

    Parameters:
        recommended_setpoints (dict): Dictionary of recommended setpoint values
        df (pd.DataFrame): DataFrame with the current data

    Returns:
        dict: Clipped control values
    """
    clipped_setpoints = {}

    for setpoint_name, recommended_value in recommended_setpoints.items():
        if setpoint_name in df.columns and len(df) > 0:
            last_value = df[setpoint_name].iloc[-1]

            # Calculate ±10% bounds
            lower_bound = last_value * 0.9
            upper_bound = last_value * 1.1

            # Clip the recommended value
            clipped_value = np.clip(recommended_value, lower_bound, upper_bound)
            clipped_setpoints[setpoint_name] = clipped_value

            if clipped_value != recommended_value:
                logging.info(
                    f"Clipped {setpoint_name}: {recommended_value:.4f} → {clipped_value:.4f} "
                    f"(last value: {last_value:.4f}, bounds: {lower_bound:.4f}-{upper_bound:.4f})"
                )
        else:
            clipped_setpoints[setpoint_name] = recommended_value

    return clipped_setpoints


def create_evidence_plots(df: pd.DataFrame) -> list:
    """
    Create evidence plots from the last 10 minutes of data.

    Parameters:
        df (pd.DataFrame): DataFrame with timestamp index and data columns

    Returns:
        list: List of LineChart objects
    """
    charts = []

    # Get data from the last 10 minutes
    if len(df) > 0:
        last_timestamp = df.index[-1]
        time_window = timedelta(minutes=10)
        window_start = last_timestamp - time_window
        df_window = df[df.index >= window_start]

        # Plot 1: exhaust_fan_3_burner_temperature_set_point and air_dryer_temperature_set_point
        series_1 = []
        for col in ["exhaust_fan_3_burner_temperature_set_point", "air_dryer_temperature_set_point"]:
            if col in df_window.columns:
                data = [
                    [int(date.timestamp() * 1000), round(float(value), 2)]
                    for date, value in zip(df_window.index, df_window[col].values)
                    if pd.notna(value)
                ]
                if data:
                    series_1.append(
                        {
                            "name": col,
                            "data": data,
                        }
                    )

        if series_1:
            charts.append(
                LineChart(
                    title="Temperature Set Points (Last 10 Minutes)",
                    timestamp=datetime.now(timezone.utc),
                    xAxis={"type": "datetime", "title": "Time"},
                    yAxis={"title": "Temperature"},
                    series=series_1,
                )
            )

        # Plot 2: shoe_press_hydration_tank_level and top_dryers_steam_pressure_set_point
        series_2 = []
        for col in ["shoe_press_hydration_tank_level", "top_dryers_steam_pressure_set_point"]:
            if col in df_window.columns:
                data = [
                    [int(date.timestamp() * 1000), round(float(value), 2)]
                    for date, value in zip(df_window.index, df_window[col].values)
                    if pd.notna(value)
                ]
                if data:
                    series_2.append(
                        {
                            "name": col,
                            "data": data,
                        }
                    )

        if series_2:
            charts.append(
                LineChart(
                    title="Hydration Level and Pressure (Last 10 Minutes)",
                    timestamp=datetime.now(timezone.utc),
                    xAxis={"type": "datetime", "title": "Time"},
                    yAxis={"title": "Value"},
                    series=series_2,
                )
            )

    return charts


async def process_data(app: KelvinApp, asset: str, df: pd.DataFrame) -> None:
    try:
        # Run model
        recommended_setpoints = run_model(df)

        if recommended_setpoints:
            # Clip control values to ±10% of last known value
            clipped_setpoints = clip_control_values(recommended_setpoints, df)

            # Create a control change for each recommended set point
            control_changes = []
            for setpoint_name, value in clipped_setpoints.items():
                control_change = ControlChange(
                    resource=KRNAssetDataStream(asset=asset, data_stream=setpoint_name),
                    payload=value,
                    expiration_date=timedelta(hours=1),
                )
                control_changes.append(control_change)

            # Create evidence plots
            evidence_charts = create_evidence_plots(df)

            # Create and Publish a Recommendation with all control changes and evidence plots
            await app.publish(
                Recommendation(
                    resource=KRNAsset(asset=asset),
                    type="multi_objective_optimization",
                    description="Multi Objective Optimization",
                    control_changes=control_changes,
                    evidences=evidence_charts,
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
    rolling_window = RollingWindow(max_data_points=500, timestamp_rounding_interval=timedelta(seconds=1))

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
