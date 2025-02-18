import asyncio
import os
from pathlib import Path

from databricks.sdk import WorkspaceClient, useragent

import job

# Set user agent
PRODUCT = "kelvin-databricks-volume-uploader"
PRODUCT_VERSION = "1.0.0"

useragent.with_partner("kelvin.ai")
useragent.with_product(PRODUCT, version=PRODUCT_VERSION)


class DatabricksUCVolumeUploader:
    """
    A utility class for uploading files to Databricks Unity Catalog Volume.

    This class is responsible for managing the connection to a Databricks workspace and
    uploading files to a specified volume within Databricks UC. It also facilitates the setup
    of data ingestion jobs using SQL or Python based on the provided warehouse or cluster ID.
    """

    def __init__(self):
        """
        Initialize the DatabricksUCVolumeUploader instance by reading environment variables.

        Attributes:
            server_hostname (str): Databricks server hostname, retrieved from DATABRICKS_SERVER_HOSTNAME.
            access_token (str): Databricks access token, retrieved from DATABRICKS_ACCESS_TOKEN.
            client_id (str): OAuth client ID for Databricks, retrieved from DATABRICKS_CLIENT_ID.
            client_secret (str): OAuth client secret for Databricks, retrieved from DATABRICKS_CLIENT_SECRET.
            uc_volume (str): The Databricks UC volume in catalog.schema.volume format, retrieved from DATABRICKS_UC_VOLUME.
            delta_table (str): The Delta table in catalog.schema.table format, retrieved from DATABRICKS_DELTA_TABLE.
            job_cluster_id (str): Cluster ID for job setup, retrieved from DATABRICKS_JOB_CLUSTER_ID.
            job_warehouse_id (str): Warehouse ID for job setup, retrieved from DATABRICKS_JOB_WAREHOUSE_ID.
        """
        self.server_hostname = os.getenv("DATABRICKS_SERVER_HOSTNAME")
        self.access_token = os.getenv("DATABRICKS_ACCESS_TOKEN")
        self.client_id = os.getenv("DATABRICKS_CLIENT_ID")
        self.client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
        self.uc_volume = os.getenv("DATABRICKS_UC_VOLUME")
        self.delta_table = os.getenv("DATABRICKS_DELTA_TABLE")
        self.job_cluster_id = os.getenv("DATABRICKS_JOB_CLUSTER_ID")
        self.job_warehouse_id = os.getenv("DATABRICKS_JOB_WAREHOUSE_ID")

    def _workspace_client(self):
        """
        Create and return a Databricks WorkspaceClient instance.

        Returns:
            WorkspaceClient: An instance of WorkspaceClient configured with either an access token or OAuth credentials.

        Raises:
            ValueError: If neither access token nor OAuth credentials are provided.
        """
        if self.access_token:
            print("Creating WorkspaceClient using access token...")
            return WorkspaceClient(
                host=self.server_hostname,
                token=self.access_token,
                product=PRODUCT,
                product_version=PRODUCT_VERSION,
            )

        if self.client_id and self.client_secret:
            print("Creating WorkspaceClient using OAuth credentials...")
            return WorkspaceClient(
                host=self.server_hostname,
                client_id=self.client_id,
                client_secret=self.client_secret,
                product=PRODUCT,
                product_version=PRODUCT_VERSION,
            )

        raise ValueError("No valid credentials provided for Databricks")

    async def setup(self) -> None:
        """
        Asynchronous wrapper for the `_setup` method to facilitate job setup in the Databricks environment.
        """
        await asyncio.to_thread(self._setup)

    def _setup(self) -> None:
        """
        Set up a Databricks data ingestion job for the specified UC volume and Delta table.

        Raises:
            ValueError: If required environment variables for UC volume or Delta table are not provided.

        Behavior:
            Depending on the availability of `job_warehouse_id` or `job_cluster_id`, this method sets up either an
            COPY INTO job or a Auto Loader job.
        """
        if not self.uc_volume:
            raise ValueError("Please set DATABRICKS_UC_VOLUME env variable")

        if not self.delta_table:
            raise ValueError("Please set DATABRICKS_DELTA_TABLE env variable")

        if self.job_warehouse_id:
            w = self._workspace_client()
            job.create_job_copy_into(w=w, volume=self.uc_volume, table=self.delta_table, warehouse_id=self.job_warehouse_id)
        elif self.job_cluster_id:
            w = self._workspace_client()
            job.create_job_autoloader(w=w, volume=self.uc_volume, table=self.delta_table, cluster_id=self.job_cluster_id)
        else:
            print("Skipping job creation because env vars DATABRICKS_WAREHOUSE_ID and DATABRICKS_CLUSTER_ID were not provided")

    async def upload(self, file_path: str) -> None:
        """
        Asynchronous wrapper for the `_upload` method to facilitate file upload to the Databricks volume.

        Args:
            file_path (str): The local file path of the file to be uploaded.
        """
        await asyncio.to_thread(self._upload, file_path)

    def _upload(self, file_path: str) -> None:
        """
        Upload a local file to the specified Databricks UC volume path.

        Args:
            file_path (str): The local file path of the file to be uploaded.

        Behavior:
            The file is uploaded to the specified UC volume path, which is constructed based on the UC volume and file name.

        Raises:
            ValueError: If the upload fails or if no credentials are available.
        """
        # Create volume path
        catalog_name, schema_name, volume_name = self.uc_volume.split(".")
        volume_path = f"/Volumes/{catalog_name}/{schema_name}/{volume_name}/data/{Path(file_path).name}"

        print(f"Uploading file to Databricks path: '{volume_path}'")

        with open(file_path, "rb") as f:
            w = self._workspace_client()
            w.dbfs.upload(path=volume_path, src=f, overwrite=True)

        print(f"Successfully uploaded file to Databricks path: '{volume_path}'")
