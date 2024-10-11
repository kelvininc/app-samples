import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Optional, Tuple, Union

import duckdb

if TYPE_CHECKING:
    import pandas as pd


class TimeseriesDataStore:
    """
    A class to manage a time series database using DuckDB. It supports asynchronous
    operations for setting up the database, inserting data, exporting data, and trimming
    old data.

    Attributes:
        db_path (str): The file path to the DuckDB database. Defaults to in-memory database.
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        Initializes the TimeseriesDataStore with a database path.

        Args:
            db_path (str): The path to the DuckDB database file. Defaults to in-memory database.
        """
        self.db_path = db_path

    async def setup(self):
        """
        Asynchronously sets up the database by creating the `timeseries` table if it does not exist.
        """
        await asyncio.to_thread(self._setup)

    def _setup(self):
        """
        Synchronously sets up the database by creating the `timeseries` table if it does not exist.
        """
        print(f"Setting up timeseries database: '{self.db_path}'")

        with duckdb.connect(self.db_path) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS timeseries (
                    timestamp DATETIME, 
                    asset STRING, 
                    datastream STRING, 
                    payload DOUBLE,
                    PRIMARY KEY (timestamp, asset, datastream)
                )
                """
            )

        print(f"Successfully created timeseries database: '{self.db_path}'")

    async def insert(self, timestamp: datetime, asset: str, datastream: str, payload: Union[float, str, bool]):
        """
        Asynchronously inserts or updates a record in the `timeseries` table.

        Args:
            timestamp (datetime): The timestamp of the data.
            asset (str): The asset identifier.
            datastream (str): The datastream identifier.
            payload (Union[float, str, bool]): The payload data, which can be a number, string, or boolean.
        """
        await asyncio.to_thread(self._insert, timestamp, asset, datastream, payload)

    def _insert(self, timestamp: datetime, asset: str, datastream: str, payload: Union[float, str, bool]):
        """
        Synchronously inserts or updates a record in the `timeseries` table.

        Args:
            timestamp (datetime): The timestamp of the data.
            asset (str): The asset identifier.
            datastream (str): The datastream identifier.
            payload (Union[float, str, bool]): The payload data, which can be a number, string, or boolean.
        """
        with duckdb.connect(self.db_path) as con:
            con.execute(
                """
                INSERT INTO timeseries (timestamp, asset, datastream, payload) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT (timestamp, asset, datastream)
                DO UPDATE SET payload = excluded.payload
                """,
                (timestamp, asset, datastream, payload),
            )

    def _export_data(
        self, file_path: Optional[str] = None, limit: Optional[int] = None, format: Optional[str] = None
    ) -> Optional[Union[Tuple[str, int], Tuple["pd.DataFrame", int], Tuple[Dict[str, Union[str, float, bool]], int]]]:
        """
        Exports data from the `timeseries` table based on the specified format and limit.

        Args:
            file_path (Optional[str]): The file path to save the exported data, if applicable.
            limit (Optional[int]): The maximum number of records to export. If None, all available records will be exported.
            format (Optional[str]): The format to export the data in ('parquet', 'csv', 'df' for DataFrame, or None for dictionary).

        Returns:
            Optional[Union[Tuple[str, int], Tuple[pd.DataFrame, int], Tuple[Dict[str, Union[str, float, bool]], int]]]:
            A tuple containing the exported data in the specified format and the number of records exported:
            - For 'parquet' or 'csv', the first element is the file path, and the second element is the number of records.
            - For 'df', the first element is a Pandas DataFrame, and the second element is the number of records.
            - For None (default dictionary export), the first element is a dictionary and the second element is the number of records.
        """
        print(f"Exporting database values into {format} {'file: ' + file_path if file_path else ''}")

        query = f"""
            SELECT timestamp, asset, datastream, payload
            FROM timeseries
            ORDER BY timestamp ASC
            {'LIMIT ' + str(limit) if limit is not None else ''}
        """

        with duckdb.connect(self.db_path) as con:
            result = con.query(query)

            if len(result) == 0:
                print("Skipping database export because query returned 0 values")
                return None, 0

            print(f"Successfully exported {len(result)} database values to {format} {'file: ' + file_path if file_path else ''}")

            if format == "parquet":
                result.to_parquet(file_path)
                return file_path, len(result)
            elif format == "csv":
                result.to_csv(file_path)
                return file_path, len(result)
            elif format == "df":
                df = result.to_df()
                return df, len(result)
            else:
                data = [{"timestamp": row[0], "asset": row[1], "datastream": row[2], "payload": row[3]} for row in result]
                return data, len(result)

    async def export_parquet(self, file_path: str, limit: Optional[int] = None) -> Optional[Tuple[str, int]]:
        """
        Asynchronously exports data to a Parquet file.

        Args:
            file_path (str): The file path to save the Parquet file.
            limit (Optional[int]): The maximum number of records to export. If None, all available records will be exported.

        Returns:
            Optional[Tuple[str, int]]: A tuple containing the file path to the saved Parquet file and the number of records exported.
        """
        return await asyncio.to_thread(self._export_data, file_path, limit, format="parquet")

    async def export_csv(self, file_path: str, limit: Optional[int] = None) -> Optional[Tuple[str, int]]:
        """
        Asynchronously exports data to a CSV file.

        Args:
            file_path (str): The file path to save the CSV file.
            limit (Optional[int]): The maximum number of records to export. If None, all available records will be exported.

        Returns:
            Optional[Tuple[str, int]]: A tuple containing the file path to the saved CSV file and the number of records exported.
        """
        return await asyncio.to_thread(self._export_data, file_path, limit, format="csv")

    async def export_df(self, limit: Optional[int] = None) -> Optional[Tuple["pd.DataFrame", int]]:
        """
        Asynchronously exports data to a Pandas DataFrame.

        Args:
            limit (Optional[int]): The maximum number of records to export. If None, all available records will be exported.

        Returns:
            Optional[Tuple[pd.DataFrame, int]]: A tuple containing a Pandas DataFrame with the exported data and the number of records exported.
        """
        return await asyncio.to_thread(self._export_data, format="df", limit=limit)

    async def export_dict(self, limit: Optional[int] = None) -> Optional[Tuple[Dict[str, Union[str, float, bool]], int]]:
        """
        Asynchronously exports data to a dictionary.

        Args:
            limit (Optional[int]): The maximum number of records to export. If None, all available records will be exported.

        Returns:
            Optional[Tuple[Dict[str, Union[str, float, bool]], int]]: A tuple containing a dictionary with the exported data and the number of records exported.
        """
        return await asyncio.to_thread(self._export_data, limit=limit)

    async def trim(self, limit: Optional[int] = None):
        """
        Asynchronously trims the oldest records from the `timeseries` table.

        Args:
            limit (Optional[int]): The number of oldest records to delete. If None, all records are deleted.
        """
        await asyncio.to_thread(self._trim, limit)

    def _trim(self, limit: Optional[int]):
        """
        Synchronously trims the oldest records from the `timeseries` table.

        Args:
            limit (Optional[int]): The number of oldest records to delete. If None, all records are deleted.
        """
        print("Trimming database values")

        if limit is None:
            query = "DELETE FROM timeseries;"
        else:
            query = f"""
            WITH rows_to_delete AS (
                SELECT timestamp, asset, datastream
                FROM timeseries
                ORDER BY timestamp ASC
                LIMIT {limit}
            )
            DELETE FROM timeseries
            USING rows_to_delete
            WHERE timeseries.timestamp = rows_to_delete.timestamp
                  AND timeseries.asset = rows_to_delete.asset
                  AND timeseries.datastream = rows_to_delete.datastream;
            """

        with duckdb.connect(self.db_path) as con:
            con.execute(query)

        print("Successfully trimmed database values")
