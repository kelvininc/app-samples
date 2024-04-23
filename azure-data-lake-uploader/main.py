import asyncio
import os
from datetime import datetime

import aiofiles
import aiofiles.os
from kelvin.application import KelvinApp, filters
from kelvin.message import Number
from timeseries import TimeseriesDB
from uploader import AzureDataLakeStorageUploader


async def upload(app: KelvinApp, tsdb: TimeseriesDB, adls_uploader: AzureDataLakeStorageUploader):

    # Get upload interval
    upload_interval = app.app_configuration.get("upload_interval", 30)

    # Create export dir if needed
    export_dir = "export"
    await aiofiles.os.makedirs(export_dir, exist_ok=True)

    # Azure container dir
    container_dir = ""

    while True:
        # Upload all values up to this date
        cutoff = datetime.now()

        # Create filename
        date_suffix = cutoff.strftime("%Y-%m-%d_%H%M%S")
        filename = f"export_{date_suffix}.parquet"
        output_file_path = os.path.join(export_dir, filename)

        try:
            # Export data from timeseries db to file
            tsdb.export(output_file_path=output_file_path, cutoff=cutoff, format="parquet")

            # Upload file if exists
            if await aiofiles.os.path.exists(output_file_path):
                await adls_uploader.upload(file_path=output_file_path, dest_dir=container_dir)

                # We should only trim timeseries db if upload was successfully
                tsdb.trim(cutoff=cutoff)
        except Exception as e:
            print(f"error occurred: {e}")
        finally:
            # Remove file if exists
            if await aiofiles.os.path.exists(output_file_path):
                await aiofiles.os.remove(output_file_path)

            # Sleep
            await asyncio.sleep(upload_interval)


async def main() -> None:

    # Configure the Azure Data Lake Uploader
    account_name = os.getenv("AZURE_ACCOUNT_NAME")
    account_key = os.getenv("AZURE_ACCOUNT_KEY")
    container_name = os.getenv("AZURE_STORAGE_CONTAINER")

    adls_uploader = AzureDataLakeStorageUploader(account_name=account_name, account_key=account_key, container_name=container_name)

    # Configure the Timeseries Database
    tsdb = TimeseriesDB("data.db")
    tsdb.setup()

    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    # Create task to continuously upload data
    asyncio.create_task(upload(app=app, tsdb=tsdb, adls_uploader=adls_uploader))

    # Create a Filtered Queue
    msg_queue: asyncio.Queue[Number] = app.filter(filters.is_asset_data_message)

    while True:
        # Get msg from queue
        msg = await msg_queue.get()

        # Insert to local timeseries db
        tsdb.insert(timestamp=msg.timestamp, asset=msg.resource.asset, datastream=msg.resource.data_stream, payload=msg.payload)


if __name__ == "__main__":
    asyncio.run(main())
