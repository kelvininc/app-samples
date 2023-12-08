from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd
from kelvin.message import Message


def round_timestamp(dt: datetime, delta: timedelta) -> datetime:
    """
    Round a datetime object to the nearest interval specified by delta.

    Parameters:
        dt (datetime): The datetime object to round.
        delta (timedelta): The rounding interval.

    Returns:
        datetime: The rounded datetime object.
    """
    # Convert the delta to total seconds for rounding
    delta_seconds = delta.total_seconds()

    # Round the timestamp's seconds down to the nearest interval
    rounded_seconds = (dt.timestamp() // delta_seconds) * delta_seconds

    # Convert the rounded seconds back to a datetime object
    # Preserving the timezone information if present
    if dt.tzinfo is not None:
        return datetime.fromtimestamp(rounded_seconds, tz=dt.tzinfo)
    else:
        return datetime.fromtimestamp(rounded_seconds)


class RollingWindow:
    """
    A class to manage rolling windows of data for multiple assets.

    Attributes:
        max_size (Optional[int]): The maximum number of entries to keep in each DataFrame.
        max_duration (Optional[float]): The maximum duration (in seconds) of the window for each DataFrame.
        round_to (Optional[timedelta]): The rounding interval for timestamps.
        dataframes (Dict[str, pd.DataFrame]): A dictionary mapping assets to their corresponding DataFrames.
    """

    def __init__(
        self,
        max_size: Optional[int] = None,
        max_duration: Optional[float] = None,
        round_to: Optional[timedelta] = None,
    ):
        """
        Constructs all the necessary attributes for the RollingWindow object.

        Parameters:
            max_size (Optional[int]): The maximum number of rows allowed in each DataFrame.
            max_duration (Optional[float]): The maximum time duration for data in each DataFrame.
            round_to (Optional[timedelta]): The interval to which timestamp should be rounded.
        """
        self.max_size = max_size
        self.max_duration = max_duration
        self.round_to = round_to
        self.dataframes: Dict[str, pd.DataFrame] = {}

    def add_message(self, message: Message):
        """
        Adds a new message to the rolling window for the corresponding asset.

        Parameters:
            message (Message): The message containing data to be added to the rolling window.
        """
        # Extract required information from the message
        asset = message.resource.asset
        datastream = message.resource.data_stream
        value = message.payload
        timestamp = message.timestamp

        # Round the timestamp if a round_to interval was specified
        if self.round_to is not None:
            timestamp = round_timestamp(timestamp, self.round_to)

        # Check if asset DataFrame exists, if not, create it with timestamp as index
        if asset not in self.dataframes:
            self.dataframes[asset] = pd.DataFrame(columns=[datastream])
            self.dataframes[asset].index.name = "timestamp"

        # If the timestamp already exists, update the value, otherwise append a new row
        self.dataframes[asset].loc[timestamp, datastream] = value

        # Ensure the DataFrame is sorted by timestamp
        self.dataframes[asset] = self.dataframes[asset].sort_index()

        # Enforce window size constraint
        if (
            self.max_size is not None
            and self.max_size > 0
            and len(self.dataframes[asset]) > self.max_size
        ):
            # Remove the oldest entry to maintain the size constraint
            self.dataframes[asset].drop(self.dataframes[asset].index[0], inplace=True)

        # Enforce time window constraint
        if self.max_duration is not None and self.max_duration > 0:
            # Calculate the cutoff time
            cutoff_time = self.dataframes[asset].index[-1] - timedelta(
                seconds=self.max_duration
            )
            # Keep rows that are within the time window
            self.dataframes[asset] = self.dataframes[asset][
                self.dataframes[asset].index >= cutoff_time
            ]

    def get_dataframe(self, asset: str) -> pd.DataFrame:
        """
        Retrieve the DataFrame for a given asset.

        Parameters:
            asset (str): The asset identifier for which the DataFrame is required.

        Returns:
            pd.DataFrame: The DataFrame associated with the given asset.
        """
        return self.dataframes.get(asset, pd.DataFrame())

    def get_dataframes(self) -> Dict[str, pd.DataFrame]:
        """
        Retrieve all stored DataFrames.

        Returns:
            Dict[str, pd.DataFrame]: A dictionary containing all the DataFrames.
        """
        return self.dataframes
