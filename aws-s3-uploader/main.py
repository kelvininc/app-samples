import asyncio
from datetime import datetime

import aiofiles
import aiofiles.os
from kelvin.application import KelvinApp, filters
from timeseries import TimeseriesDataStore
from uploader import AWSS3Uploader


async def upload(app: KelvinApp, data_store: TimeseriesDataStore, uploader: AWSS3Uploader):
    # Create export dir
    await aiofiles.os.makedirs("export/", exist_ok=True)

    while True:
        batch_size = app.app_configuration.get("batch_size", 1000)
        upload_interval = app.app_configuration.get("upload_interval", 30)

        # Create filename
        export_file = f"export/{datetime.now().isoformat()}.parquet"

        try:
            # Export to parquet file
            _, chunk_size = await data_store.export_parquet(file_path=export_file, limit=batch_size)

            # Upload file if exists
            if await aiofiles.os.path.exists(export_file):
                await uploader.upload(file_path=export_file)

                # We should only trim data store if upload was successfully
                await data_store.trim(limit=batch_size)

                # Skip sleep if batch_size was full
                if chunk_size < batch_size:
                    print("No more data to upload, waiting for next interval.")
                    await asyncio.sleep(upload_interval)
                else:
                    print("Chunk was full, continuing to process without sleeping.")
            else:
                print("No data to upload at this time.")
                await asyncio.sleep(upload_interval)

        except Exception as e:
            print(f"Error occurred during upload: {e}")
            await asyncio.sleep(upload_interval)
        finally:
            # Remove file if exists
            if await aiofiles.os.path.exists(export_file):
                await aiofiles.os.remove(export_file)


async def main() -> None:
    # Configure the AWS S3 Uploader
    uploader = AWSS3Uploader()

    # Configure the Timeseries Database
    data_store = TimeseriesDataStore("data.db")
    await data_store.setup()

    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    # Create task to continuously upload data
    asyncio.create_task(upload(app=app, data_store=data_store, uploader=uploader))

    # Subscribe to the asset data streams
    async for msg in app.stream_filter(filters.is_asset_data_message):
        # Insert msg to local data store
        await data_store.insert(timestamp=msg.timestamp, asset=msg.resource.asset, datastream=msg.resource.data_stream, payload=msg.payload)


if __name__ == "__main__":
    asyncio.run(main())
