import asyncio
import os

import pandas as pd
from databricks import sql
from databricks.sdk.core import Config, oauth_service_principal

USER_AGENT = "Kelvin.ai"


class DatabricksDeltaTableUploader:
    """
    A class to manage uploading data to a Delta table in Databricks.

    The class supports setting up the Delta table and uploading Pandas DataFrames asynchronously.

    Attributes:
        server_hostname (Optional[str]): The hostname of the Databricks server.
        http_path (Optional[str]): The HTTP path for the Databricks SQL endpoint.
        access_token (Optional[str]): The access token for authentication.
        client_id (Optional[str]): The client ID for OAuth authentication.
        client_secret (Optional[str]): The client secret for OAuth authentication.
        table_name (Optional[str]): The name of the Delta table in Databricks.
    """

    def __init__(self):
        """
        Initializes the DatabricksDeltaTableUploader by loading environment variables.
        """
        self.server_hostname = os.getenv("DATABRICKS_SERVER_HOSTNAME")
        self.http_path = os.getenv("DATABRICKS_HTTP_PATH")
        self.access_token = os.getenv("DATABRICKS_ACCESS_TOKEN")
        self.client_id = os.getenv("DATABRICKS_CLIENT_ID")
        self.client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
        self.table_name = os.getenv("DATABRICKS_TABLE_NAME")

    def _credential_provider(self) -> oauth_service_principal:
        """
        Provides OAuth credentials for authentication.

        Returns:
            oauth_service_principal: The OAuth service principal for authentication.
        """
        config = Config(host=f"https://{self.server_hostname}", client_id=self.client_id, client_secret=self.client_secret)
        return oauth_service_principal(config)

    def _connect(self):
        """
        Establishes a connection to the Databricks SQL endpoint using the available credentials.

        Returns:
            sql.Connection: A connection object for executing SQL queries.

        Raises:
            ValueError: If no valid credentials are provided.
        """
        if self.access_token:
            print("Connecting using access token...")
            return sql.connect(server_hostname=self.server_hostname, http_path=self.http_path, access_token=self.access_token, _user_agent_entry=USER_AGENT)

        if self.client_id and self.client_secret:
            print("Connecting using OAuth credentials...")
            return sql.connect(
                server_hostname=self.server_hostname, http_path=self.http_path, credentials_provider=self._credential_provider, _user_agent_entry=USER_AGENT
            )

        raise ValueError("No valid credentials provided for Databricks connection")

    async def setup(self) -> None:
        """
        Asynchronously sets up the Delta table by creating it if it does not exist.
        """
        await asyncio.to_thread(self._setup)

    def _setup(self) -> None:
        """
        Synchronously sets up the Delta table by creating it if it does not exist.
        """
        print(f"Setting up Delta table: '{self.table_name}'")

        # SQL query to create the Delta table
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {self.table_name} 
        (timestamp TIMESTAMP, asset STRING, datastream STRING, payload DOUBLE)
        USING DELTA;
        """

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(create_table_query)

        print(f"Successfully created Delta table: '{self.table_name}'")

    async def upload(self, df: pd.DataFrame) -> None:
        """
        Asynchronously uploads a Pandas DataFrame to the Delta table.

        Args:
            df (pd.DataFrame): The DataFrame containing the data to upload.
        """
        await asyncio.to_thread(self._upload, df)

    def _upload(self, df: pd.DataFrame) -> None:
        """
        Synchronously uploads a Pandas DataFrame to the Delta table.

        Args:
            df (pd.DataFrame): The DataFrame containing the data to upload.
        """
        print(f"Uploading DataFrame with {len(df)} records to Delta table: '{self.table_name}'")

        # Convert Timestamps to strings
        df["timestamp"] = df["timestamp"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S.%f") if isinstance(x, pd.Timestamp) else x)

        # Generate the SQL INSERT query
        rows = [f"('{row[0]}', '{row[1]}', '{row[2]}', {row[3]})" for row in df.itertuples(index=False, name=None)]
        insert_query = f"""
        INSERT INTO {self.table_name} (timestamp, asset, datastream, payload)
        VALUES {', '.join(rows)}
        """

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(insert_query)

        print(f"Successfully uploaded {len(df)} records to Delta table: '{self.table_name}'")
