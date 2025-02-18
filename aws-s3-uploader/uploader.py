import asyncio
import os
import posixpath

import aiofiles
import aiofiles.os
import boto3


class AWSS3Uploader:
    def __init__(self):
        # Read AWS credentials from environment variables (or rely on AWS's default credential chain)
        self.aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        self.region_name = os.getenv("AWS_REGION", "us-east-1")  # Default region can be changed as needed
        self.bucket_name = os.getenv("AWS_S3_BUCKET")

        # Create the boto3 S3 client
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.region_name,
        )

    async def upload(self, file_path: str, dest_dir: str = ""):
        """Asynchronously upload a file to an S3 bucket."""

        print(f"Uploading file '{file_path}' to S3 bucket: '{self.bucket_name}'")

        # Check if the local file exists
        if not await aiofiles.os.path.exists(file_path):
            print(f"Upload skipped. File '{file_path}' does not exist.")
            return

        # Build the destination key (S3 object name)
        file_name = posixpath.basename(file_path)
        dest_file_path = posixpath.join(dest_dir, file_name)

        print(f"Destination S3 key: '{dest_file_path}'")

        await asyncio.to_thread(self._upload_file, file_path, dest_file_path)

        print(f"Successfully uploaded file '{file_path}' to '{dest_file_path}' in bucket '{self.bucket_name}'.")

    def _upload_file(self, file_path: str, dest_file_path: str):
        with open(file_path, "rb") as data:
            print(f"Uploading file '{file_path}' to '{dest_file_path}' in bucket '{self.bucket_name}'...")
            self.s3_client.upload_fileobj(data, self.bucket_name, dest_file_path)
