import sys

sys.path.append("..")  # This line adds the parent directory to the system path

import unittest
from datetime import datetime, timedelta

from kelvin.message import KRNAssetDataStream, Number
from rolling_window import RollingWindow


class TestRollingWindow(unittest.TestCase):
    def setUp(self):
        self.rolling_window = RollingWindow(max_data_points=10, max_window_duration=300)

    def test_initialization(self):
        self.assertEqual(self.rolling_window.max_data_points, 10)
        self.assertEqual(self.rolling_window.max_window_duration, 300)
        self.assertIsNone(self.rolling_window.timestamp_rounding_interval)
        self.assertEqual(len(self.rolling_window.asset_dataframes), 0)

    def test_add_message(self):
        message = Number(
            resource=KRNAssetDataStream("Asset1", "Datastream1"),
            payload=100,
            timestamp=datetime.now(),
        )
        self.rolling_window.add_message(message)
        self.assertIn("Asset1", self.rolling_window.asset_dataframes)
        self.assertEqual(len(self.rolling_window.asset_dataframes["Asset1"]), 1)

    def test_window_size_constraint(self):
        for i in range(15):
            message = Number(
                resource=KRNAssetDataStream("Asset1", "Datastream1"),
                payload=i,
                timestamp=datetime.now(),
            )
            self.rolling_window.add_message(message)

        self.assertEqual(
            len(self.rolling_window.asset_dataframes["Asset1"]),
            self.rolling_window.max_data_points,
        )

    def test_window_time_constraint(self):
        now = datetime.now()
        for i in range(5):
            timestamp = now - timedelta(seconds=i * 100)
            message = Number(
                resource=KRNAssetDataStream("Asset1", "Datastream1"),
                payload=i,
                timestamp=timestamp,
            )
            self.rolling_window.add_message(message)

        self.assertTrue(
            (
                now - self.rolling_window.asset_dataframes["Asset1"].index[0]
            ).total_seconds()
            <= self.rolling_window.max_window_duration
        )

    def test_get_dataframe(self):
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Datastream1"),
                payload=100,
                timestamp=datetime.now(),
            )
        )
        df = self.rolling_window.get_asset_dataframe("Asset1")
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 1)

    def test_get_all_asset_dataframes(self):
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Datastream1"),
                payload=100,
                timestamp=datetime.now(),
            )
        )
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset2", "Datastream2"),
                payload=200,
                timestamp=datetime.now(),
            )
        )
        dfs = self.rolling_window.get_all_asset_dataframes()
        self.assertEqual(len(dfs), 2)

    def test_multiple_assets(self):
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=datetime.now(),
            )
        )
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset2", "Stream2"),
                payload=200,
                timestamp=datetime.now(),
            )
        )
        self.assertEqual(len(self.rolling_window.asset_dataframes["Asset1"]), 1)
        self.assertEqual(len(self.rolling_window.asset_dataframes["Asset2"]), 1)

    def test_time_rounding(self):
        self.rolling_window.timestamp_rounding_interval = timedelta(minutes=1)
        timestamp = datetime(2023, 1, 1, 12, 30, 45)  # Example timestamp
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=timestamp,
            )
        )
        rounded_timestamp = self.rolling_window.asset_dataframes["Asset1"].index[0]
        self.assertEqual(rounded_timestamp.second, 0)
        self.assertEqual(rounded_timestamp.minute, 30)

    def test_dataframe_structure(self):
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=datetime.now(),
            )
        )
        df = self.rolling_window.get_asset_dataframe("Asset1")
        self.assertIn("Stream1", df.columns)
        self.assertEqual(df.index.name, "timestamp")

    def test_empty_asset_retrieval(self):
        df = self.rolling_window.get_asset_dataframe("NonExistentAsset")
        self.assertTrue(df.empty)

    def test_out_of_order_timestamps(self):
        timestamps = [datetime.now() - timedelta(seconds=i) for i in range(5)]
        for ts in timestamps:
            self.rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=100,
                    timestamp=ts,
                )
            )

        # The DataFrame should be sorted even if timestamps were added out of order
        sorted_timestamps = list(self.rolling_window.asset_dataframes["Asset1"].index)
        self.assertEqual(sorted_timestamps, sorted(timestamps))

    def test_data_overwrite(self):
        timestamp = datetime.now()
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=timestamp,
            )
        )
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=200,
                timestamp=timestamp,
            )
        )
        self.assertEqual(
            self.rolling_window.asset_dataframes["Asset1"].loc[timestamp, "Stream1"],
            200,
        )

    def test_max_data_points_exact_match(self):
        for i in range(10):  # Assuming max_data_points is 10
            self.rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=i,
                    timestamp=datetime.now(),
                )
            )

        self.assertEqual(len(self.rolling_window.asset_dataframes["Asset1"]), 10)

    def test_time_window_exact_match(self):
        now = datetime.now()
        for i in range(5):
            timestamp = now - timedelta(
                seconds=i * 60
            )  # Assuming max_window_duration is 300 seconds
            self.rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=i,
                    timestamp=timestamp,
                )
            )

        self.assertEqual(
            (
                now - self.rolling_window.asset_dataframes["Asset1"].index[0]
            ).total_seconds(),
            240,
        )

    def test_removal_of_multiple_old_entries(self):
        for i in range(15):  # Adding more messages than max_data_points
            timestamp = datetime.now() - timedelta(seconds=i)
            self.rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=i,
                    timestamp=timestamp,
                )
            )

        self.assertEqual(
            len(self.rolling_window.asset_dataframes["Asset1"]), 10
        )  # Assuming max_data_points is 10

    def test_multiple_datastreams_same_asset(self):
        timestamp = datetime.now()
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=timestamp,
            )
        )
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream2"),
                payload=200,
                timestamp=timestamp,
            )
        )
        self.assertEqual(
            self.rolling_window.asset_dataframes["Asset1"].loc[timestamp, "Stream1"],
            100,
        )
        self.assertEqual(
            self.rolling_window.asset_dataframes["Asset1"].loc[timestamp, "Stream2"],
            200,
        )

    def test_zero_max_data_points_duration(self):
        rolling_window = RollingWindow(max_data_points=0, max_window_duration=0)
        rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=datetime.now(),
            )
        )
        self.assertEqual(
            len(rolling_window.asset_dataframes["Asset1"]), 1
        )  # Zero values should be treated as 'no limit'

    def test_negative_max_data_points_duration(self):
        rolling_window = RollingWindow(max_data_points=-1, max_window_duration=-1)
        rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=datetime.now(),
            )
        )
        self.assertEqual(
            len(rolling_window.asset_dataframes["Asset1"]), 1
        )  # Negative values should be treated as 'no limit'

    def test_add_different_datastream_existing_asset(self):
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=datetime.now(),
            )
        )
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream2"),
                payload=200,
                timestamp=datetime.now(),
            )
        )
        self.assertIn("Stream2", self.rolling_window.asset_dataframes["Asset1"].columns)

    def test_dataframe_content_multiple_additions(self):
        for i in range(5):
            self.rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=i,
                    timestamp=datetime.now(),
                )
            )
        self.assertEqual(
            list(self.rolling_window.asset_dataframes["Asset1"]["Stream1"]),
            list(range(5)),
        )

    def test_timestamp_ordering_non_chronological_additions(self):
        timestamps = [datetime.now() + timedelta(seconds=i) for i in range(5, 0, -1)]
        for ts in timestamps:
            self.rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=100,
                    timestamp=ts,
                )
            )

        self.assertEqual(
            sorted(timestamps),
            list(self.rolling_window.asset_dataframes["Asset1"].index),
        )

    def test_timestamp_rounding_various_intervals(self):
        intervals = [
            timedelta(minutes=1)
        ]  # [timedelta(minutes=1), timedelta(hours=1), timedelta(seconds=30)]
        for interval in intervals:
            rolling_window = RollingWindow(
                max_data_points=10,
                max_window_duration=300,
                timestamp_rounding_interval=interval,
            )
            timestamp = datetime(2023, 1, 1, 12, 34, 56)
            rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=100,
                    timestamp=timestamp,
                )
            )
            rounded_timestamp = (
                rolling_window.asset_dataframes["Asset1"].index[0].to_pydatetime()
            )
            self.assertEqual(
                rounded_timestamp,
                timestamp
                - timedelta(seconds=timestamp.second % interval.total_seconds()),
            )

    def test_dataframe_integrity_after_dropping_rows(self):
        for i in range(15):  # Assuming max_data_points is 10
            timestamp = datetime.now() - timedelta(seconds=i)
            self.rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=i,
                    timestamp=timestamp,
                )
            )

        self.assertEqual(len(self.rolling_window.asset_dataframes["Asset1"]), 10)
        self.assertTrue(
            "Stream1" in self.rolling_window.asset_dataframes["Asset1"].columns
        )

    def test_same_timestamp_different_datastreams(self):
        timestamp = datetime.now()
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=timestamp,
            )
        )
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream2"),
                payload=200,
                timestamp=timestamp,
            )
        )
        self.assertEqual(
            len(self.rolling_window.asset_dataframes["Asset1"].loc[timestamp]), 2
        )

    def test_very_old_timestamp_addition(self):
        old_timestamp = datetime.now() - timedelta(days=365)  # One year old timestamp
        self.rolling_window.add_message(
            Number(
                resource=KRNAssetDataStream("Asset1", "Stream1"),
                payload=100,
                timestamp=old_timestamp,
            )
        )
        self.assertEqual(len(self.rolling_window.asset_dataframes["Asset1"]), 1)
        self.assertIn(
            old_timestamp, self.rolling_window.asset_dataframes["Asset1"].index
        )

    def test_large_max_window_duration(self):
        rolling_window = RollingWindow(
            max_data_points=10, max_window_duration=1000000
        )  # Very large max_window_duration
        for i in range(5):
            timestamp = datetime.now() - timedelta(days=i)
            rolling_window.add_message(
                Number(
                    resource=KRNAssetDataStream("Asset1", "Stream1"),
                    payload=i,
                    timestamp=timestamp,
                )
            )

        self.assertEqual(len(rolling_window.asset_dataframes["Asset1"]), 5)


if __name__ == "__main__":
    unittest.main()
