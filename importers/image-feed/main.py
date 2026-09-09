import asyncio
import base64
import io
from pathlib import Path

from kelvin.application import AssetInfo, KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import KMessageTypeData, Message
from PIL import Image
from pydantic import ValidationError

from settings import Settings

app = KelvinApp()

_DEFAULT_INTERVAL = 30                    # fallback wait when config is invalid
_ICD = "camera-image"
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
Target = tuple[str, str, str]            # (asset, stream, source_dir)


def encode_image(path: str) -> dict:
    """Read an image file and return the camera-image object payload.
    The payload carries the file's exact bytes; PIL is used only to read format and dimensions."""
    source = Path(path)
    raw = source.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        return {
            "image_filename": source.name,
            "image_base64": base64.b64encode(raw).decode(),
            "image_format": image.format or "JPEG",
            "image_size": {"width": image.width, "height": image.height},
        }


def image_files(folder: str) -> list[str]:
    """Sorted list of image files in a folder (empty if the folder doesn't exist).
    Non-image files (.DS_Store, .txt, ...) are excluded so they never consume a cursor slot."""
    directory = Path(folder)
    if not directory.is_dir():
        return []
    return sorted(
        str(entry)
        for entry in directory.iterdir()
        if entry.is_file() and entry.suffix.lower() in _IMAGE_EXTENSIONS
    )


def build_targets(assets: dict[str, AssetInfo], default_dir: str) -> list[Target]:
    """One (asset, stream, source_dir) per mapped stream. Each mapped stream optionally overrides
    the image source folder via its `source_dir` IO configuration; otherwise the app default is used."""
    targets: list[Target] = []
    for asset_name, asset_info in assets.items():
        for stream_name, stream_ds in asset_info.datastreams.items():
            source_dir = stream_ds.configuration.get("source_dir") or default_dir
            targets.append((asset_name, stream_name, source_dir))
    return targets


async def main() -> None:
    await app.connect()

    cursors: dict[tuple[str, str], int] = {}          # per-(asset, stream) round-robin position
    while True:
        # Re-read config each loop so runtime configuration/mapping updates take effect.
        try:
            settings = Settings(**app.app_configuration)
        except ValidationError as e:
            logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
            await asyncio.sleep(_DEFAULT_INTERVAL)
            continue

        camera = settings.camera
        targets = build_targets(app.assets, camera.images_dir)
        if not targets:
            logger.warning("No camera streams mapped", wait_seconds=camera.publish_interval)
            await asyncio.sleep(camera.publish_interval)
            continue

        for asset, stream, source_dir in targets:
            files = await asyncio.to_thread(image_files, source_dir)
            if not files:
                logger.warning("No supported images in source directory (jpg/jpeg/png/gif/bmp)", source_dir=source_dir, asset=asset, stream=stream)
                continue

            index = cursors.get((asset, stream), 0) % len(files)
            path = files[index]
            # Store a bounded cursor (0..len-1). Trade-off: if the directory changes size between
            # cycles the stream restarts from the first image, unlike a raw counter that resumes at
            # raw % new_len. Acceptable here since the sample dataset is fixed and it keeps the counter bounded.
            cursors[(asset, stream)] = (index + 1) % len(files)

            try:
                payload = await asyncio.to_thread(encode_image, path)
            except Exception as e:        # noqa: BLE001 - skip an unreadable/corrupt image, keep streaming
                logger.warning("Failed to encode image", path=path,
                               error=str(e), error_type=type(e).__name__)
                continue

            try:
                published = await app.publish(Message(
                    type=KMessageTypeData(primitive="object", icd=_ICD),
                    resource=KRNAssetDataStream(asset, stream),
                    payload=payload,
                ))
            except RuntimeError as e:     # not connected: don't die, resume publishing next cycle
                logger.warning("Publish failed; skipping frame, publishing resumes next cycle",
                               asset=asset, stream=stream, error=str(e), error_type=type(e).__name__)
                break                     # connection is down; remaining targets would fail identically
            if not published:             # SDK returns False on ConnectionError
                logger.warning("Publish not delivered; skipping frame", asset=asset, stream=stream, image=payload["image_filename"])
                continue
            logger.debug("Published image", asset=asset, stream=stream, image=payload["image_filename"])

        await asyncio.sleep(camera.publish_interval)


if __name__ == "__main__":
    asyncio.run(main())
