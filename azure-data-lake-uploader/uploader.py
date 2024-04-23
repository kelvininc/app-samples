import posixpath

import aiofiles
import aiofiles.os
from azure.storage.filedatalake.aio import DataLakeServiceClient


class AzureDataLakeStorageUploader:

    def __init__(self, account_name: str, account_key: str, container_name: str):
        self.service_client = DataLakeServiceClient(account_url=f"https://{account_name}.dfs.core.windows.net", credential=account_key)
        self.container_name = container_name

    async def upload(self, file_path: str, dest_dir: str = ""):
        print(f"uploading file '{file_path}' to adls container: '{self.container_name}'")

        # Check if file exists
        if not await aiofiles.os.path.exists(file_path):
            print(f"upload skipped. File '{file_path}' does not exist")
            return

        # Create a client for the file system (container)
        file_system_client = self.service_client.get_file_system_client(file_system=self.container_name)
        print(f"got file system client for container: '{self.container_name}'")

        # Build the destination file path
        file_name = posixpath.basename(file_path)
        dest_file_path = posixpath.join(dest_dir, file_name)

        # Get a client for the destination file
        file_client = file_system_client.get_file_client(dest_file_path)
        print(f"got file client for destination path: '{dest_file_path}'")

        # Open the file to be uploaded asynchronously
        async with aiofiles.open(file_path, "rb") as data:
            file_contents = await data.read()
            print(f"uploading file '{file_path}' to '{dest_file_path}'")
            await file_client.upload_data(file_contents, overwrite=True)
            print(f"successfully uploaded file '{file_path}' to '{dest_file_path}'")
