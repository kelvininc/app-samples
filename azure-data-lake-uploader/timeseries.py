from datetime import datetime
from typing import Optional, Union

import duckdb


class TimeseriesDB:

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

    def export(self, output_file_path: str, cutoff: datetime, format: str = "parquet"):
        print(f"exporting database data up to '{cutoff.isoformat()}' into file: '{output_file_path}'")

        with duckdb.connect(self.db_path) as con:
            result = con.query("SELECT * FROM timeseries WHERE timestamp <= ?", params=(cutoff,))

            if len(result) > 0:
                if format == "parquet":
                    result.to_parquet(output_file_path)
                elif format == "csv":
                    result.to_csv(output_file_path)
                else:
                    raise ValueError("Invalid export format. Allowed values: 'csv' or 'parquet'")

                print(f"successfully exported {len(result)} database values to file: {output_file_path}")
            else:
                print("skipping database export because query returned 0 values")
