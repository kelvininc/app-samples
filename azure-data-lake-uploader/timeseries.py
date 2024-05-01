from datetime import datetime
from typing import Optional, Union

import duckdb


class TimeseriesDataStore:

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path

    def setup(self):
        print(f"setting up database: '{self.db_path}'")

        with duckdb.connect(self.db_path) as con:
            # Create table
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS timeseries (
                    timestamp DATETIME, 
                    asset STRING, 
                    datastream STRING, 
                    payload UNION(number DOUBLE, string VARCHAR, boolean BOOLEAN),
                    PRIMARY KEY (timestamp, asset, datastream)
                )
                """
            )

        print(f"successfully set up database: '{self.db_path}'")

    def insert(self, timestamp: datetime, asset: str, datastream: str, payload: Union[float, str, bool]):
        with duckdb.connect(self.db_path) as con:
            con.execute(
                """
                        INSERT INTO timeseries (timestamp, asset, datastream, payload) VALUES (?, ?, ?, ?)
                        ON CONFLICT (timestamp, asset, datastream)
                        DO UPDATE SET payload = excluded.payload
                        """,
                (timestamp, asset, datastream, payload),
            )

    def trim(self, cutoff: Optional[datetime]):
        print(f"triming database up to: '{cutoff.isoformat()}'")

        with duckdb.connect(self.db_path) as con:
            con.execute("DELETE FROM timeseries WHERE timestamp <= ?", (cutoff,))
            print(f"successfully trimmed database up to: '{cutoff.isoformat()}'. All entries on or before this date have been removed")

    def export_parquet(self, file_path: str, cutoff: datetime):
        print(f"exporting database data up to '{cutoff.isoformat()}' into parquet file: '{file_path}'")

        with duckdb.connect(self.db_path) as con:
            result = con.query("SELECT * FROM timeseries WHERE timestamp <= ?", params=(cutoff,))

            if len(result) == 0:
                print("skipping database export because query returned 0 values")
                return

            result.to_parquet(file_path)

            print(f"successfully exported {len(result)} database values to parquet file: {file_path}")

    def export_csv(self, file_path: str, cutoff: datetime):
        print(f"exporting database data up to '{cutoff.isoformat()}' into csv file: '{file_path}'")

        with duckdb.connect(self.db_path) as con:
            result = con.query("SELECT * FROM timeseries WHERE timestamp <= ?", params=(cutoff,))

            if len(result) == 0:
                print("skipping database export because query returned 0 values")
                return

            result.to_csv(file_path)

            print(f"successfully exported {len(result)} database values to csv file: {file_path}")

    def export_df(self, cutoff: datetime):
        print(f"exporting database data up to '{cutoff.isoformat()}' into a DataFrame")

        with duckdb.connect(self.db_path) as con:
            result = con.query("SELECT * FROM timeseries WHERE timestamp <= ?", params=(cutoff,))

            if len(result) == 0:
                print("skipping database export because query returned 0 values")
                return

            df = result.to_df()

            print(f"successfully exported {len(result)} database values to a DataFrame")

            return df
