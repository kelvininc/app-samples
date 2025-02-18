import asyncio
import base64
import io
import json
import os

from kelvin.application import KelvinApp
from kelvin.message import KMessageTypeData, Message
from kelvin.message.krn import KRNAssetDataStream
from PIL import Image


def generator_images_to_base64(folder_path):
    while True:  # Start an infinite loop
        # List all files in the directory
        for filename in os.listdir(folder_path):
            # Construct the full file path
            file_path = os.path.join(folder_path, filename)
            # Check if it is a file
            if os.path.isfile(file_path):
                # Open the image
                try:
                    with Image.open(file_path) as image:
                        # Convert the image to bytes
                        buffered = io.BytesIO()
                        image_format = image.format if image.format else "JPEG"  # Default to JPEG if format is unknown
                        image.save(buffered, format=image_format)
                        # Encode the bytes to base64
                        img_str = base64.b64encode(buffered.getvalue()).decode()
                        yield {
                            "image_filename": filename,
                            "image_base64": img_str,
                            "image_format": image_format,
                            "image_size": {
                                "width": image.width,
                                "height": image.height,
                            },
                        }
                except Exception as e:
                    print(f"Error processing {filename}: {e}")
                    continue  # Skip files that cause errors


async def main() -> None:
    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    image_generator = generator_images_to_base64("images/")

    while True:

        # Get the next image from the generator
        image = next(image_generator)

        for asset in app.assets.keys():
            print(f'publishing to asset {asset} with image {image["image_filename"]}')

            await app.publish(Message(
                type=KMessageTypeData(primitive="object", icd="camera-image"),
                resource=KRNAssetDataStream(asset, "camera-feed"),
                payload=json.dumps(image)
            )
        )

        # Custom Loop
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
