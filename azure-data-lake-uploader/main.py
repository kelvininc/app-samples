import asyncio
import os
from datetime import datetime

import aiofiles
import aiofiles.os
from kelvin.application import KelvinApp, filters
from timeseries import TimeseriesDataStore
from uploader import AzureDataLakeStorageUploader


async def upload(app: KelvinApp, data_store: TimeseriesDataStore, uploader: AzureDataLakeStorageUploader):

    # Get upload interval
    upload_interval = app.app_configuration.get("upload_interval", 30)

    # Create export dir if needed
    export_dir = "export"
    await aiofiles.os.makedirs(export_dir, exist_ok=True)

    while True:
        # Upload all values up to this date
        cutoff = datetime.now()

        # Create filename
        export_filename = "export_" + cutoff.strftime("%Y-%m-%d_%H%M%S") + ".parquet"
        export_file_path = os.path.join(export_dir, export_filename)

        try:
            # Export data from data store to file
            data_store.export_parquet(file_path=export_file_path, cutoff=cutoff)

            # Upload file if exists
            if await aiofiles.os.path.exists(export_file_path):
                await uploader.upload(file_path=export_file_path, dest_dir="")

                # We should only trim data store if upload was successfully
                data_store.trim(cutoff=cutoff)
        except Exception as e:
            print(f"error occurred: {e}")
        finally:
            # Remove file if exists
            if await aiofiles.os.path.exists(export_file_path):
                await aiofiles.os.remove(export_file_path)

            # Sleep
            await asyncio.sleep(upload_interval)


async def main() -> None:

    # Configure the Azure Data Lake Uploader
    account_name = os.getenv("AZURE_ACCOUNT_NAME")
    account_key = os.getenv("AZURE_ACCOUNT_KEY")
    container_name = os.getenv("AZURE_STORAGE_CONTAINER")

    uploader = AzureDataLakeStorageUploader(account_name=account_name, account_key=account_key, container_name=container_name)

    # Configure the Timeseries Database
    data_store = TimeseriesDataStore("data.db")
    data_store.setup()

    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    # Create task to continuously upload data
    asyncio.create_task(upload(app=app, data_store=data_store, uploader=uploader))

    # Subscribe to the asset data streams
    stream = app.stream_filter(filters.is_asset_data_message)

    async for msg in stream:
        # Insert msg to local data store
        data_store.insert(timestamp=msg.timestamp, asset=msg.resource.asset, datastream=msg.resource.data_stream, payload=msg.payload)


if __name__ == "__main__":
    asyncio.run(main())
