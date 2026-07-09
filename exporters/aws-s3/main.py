from typing import Optional

from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import AssetDataMessage
from pydantic import ValidationError

from drain import Writer, drain
from settings import Settings
from store import Store
from writer import S3Writer

app = KelvinApp()
store = Store(db_path="data.db")
_settings: Optional[Settings] = None     # set once in on_connect
_writer: Optional[Writer] = None


@app.stream
async def on_data(msg: AssetDataMessage) -> None:
    """Buffer every incoming asset data message (number/string/boolean)."""
    await store.append(msg.timestamp, msg.resource.asset, msg.resource.data_stream, msg.payload)


@app.task
async def export() -> None:
    """SDK-managed drain: started after on_connect, cancelled+awaited on disconnect."""
    if _settings is None or _writer is None:     # on_connect runs first (SDK lifecycle)
        raise RuntimeError("export task started before on_connect")  # explicit raise survives python -O
    await drain(_writer, store, _settings.upload, _settings.buffer, app.clock)


@app.on_connect
async def on_connect() -> None:
    """Validate config upfront, build buffer + writer before tasks run.
    Idempotent: store.setup() reuses an open buffer, so a re-fire won't leak."""
    global _settings, _writer
    try:
        settings = Settings(**app.app_configuration)
    except ValidationError as e:
        # errors() without input: str(e) would embed the raw value (a credential) in the log.
        logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
        raise
    _settings = settings
    await store.setup()
    if _writer is not None:                      # re-fire: drop the stale writer before rebuilding
        await _writer.teardown()
    _writer = S3Writer(settings.s3, settings.upload.format)
    await _writer.setup()


@app.on_disconnect
async def on_disconnect() -> None:
    """Symmetric teardown: main owns the writer + buffer lifecycle (the drain task doesn't)."""
    if _writer is not None:
        await _writer.teardown()
    await store.teardown()


if __name__ == "__main__":
    app.run()
