# Image Feed Importer
This application demonstrates the use of the Kelvin SDK for importing a camera image feed.

It replays a folder of images and publishes each one to Kelvin as a
`camera-image` object with the fields `image_filename`, `image_base64`, `image_format`, and `image_size`. Supported
formats are jpg, jpeg, png, gif, and bmp; other files in the folder are ignored. It simulates a
camera feed; in production it would interface with a live camera instead of a folder.

That object shape is a shared contract: the [casting-defect-detection](../../applications/casting-defect-detection)
SmartApp consumes these fields, so keep the two in sync if you change the payload.

Which Kelvin asset/stream receives the feed is **runtime configuration**: an operator maps one or
more `camera-image` streams in the Kelvin UI. The connector publishes the next image to each mapped
stream every `publish_interval` seconds, and re-reads the mapping on each cycle so changes take effect
without a redeploy. A mapped stream can override the image folder via its `source_dir` IO setting.

## IO Mapping
Each mapped stream's `io_configuration` may set:
- `source_dir`: the image folder for this stream (optional; defaults to the app-level `images_dir`).

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and passes it through as `app_configuration`.

Override the `defaults.configuration` values there:

```yaml
camera:
  images_dir: images
  publish_interval: 30
```

Run the application: `python3 main.py`. The bundled `images/` folder is used by default once a
`camera-image` stream is mapped.

## Test Locally

### Unit Tests
All tests are plain unit tests; no Docker or live platform needed:

```bash
pip3 install -r requirements.txt pytest          # runtime deps + test runner
pytest                                           # fast, no Docker
```

- **Helpers + loop** (`test_main.py`): image encoding, folder listing/filtering, target mapping, and
  the per-stream round-robin cursor exercised through `main()` with a faked app.
- **Settings** (`test_settings.py`): defaults, overrides, and validation (blank dirs, out-of-range
  intervals, unknown platform keys).

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: The `images/` folder ships in the
   container image, so the connector has frames to replay without extra setup.
