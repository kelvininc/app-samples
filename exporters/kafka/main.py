from typing import Optional

from kelvin.application import AssetInfo, KelvinApp
from kelvin.logs import logger
from kelvin.message import AssetDataMessage
from pydantic import ValidationError

from drain import Writer, drain
from settings import Settings
from store import Store
from writer import KafkaWriter

app = KelvinApp()
store = Store(db_path="data/data.db")
_settings: Optional[Settings] = None     # set once in on_connect
_writer: Optional[Writer] = None
_topics: dict[tuple[str, str], str] = {}   # (asset, datastream) -> resolved topic, built in on_connect


def resolve(template: str, asset: str, stream: str) -> str:
    """Substitute {asset}/{stream} placeholders (fleet templating); a literal topic is unchanged."""
    return template.replace("{asset}", asset).replace("{stream}", stream)


def build_topic_map(assets: dict[str, AssetInfo]) -> dict[tuple[str, str], str]:
    """Map each (asset, stream) to its (resolved) Kafka topic from per-stream IO configuration.

    A stream without a topic can't be exported: warn once here and leave it out; on_data
    then never buffers it (same ignore-unmapped contract as the MQTT/Kafka importers).
    """
    topics: dict[tuple[str, str], str] = {}
    for asset_name, asset_info in assets.items():
        for stream_name, sds in asset_info.datastreams.items():
            raw_topic = sds.configuration.get("topic")
            if not raw_topic:
                logger.warning("Stream has no topic configured; it will not be exported",
                               asset=asset_name, stream=stream_name)
                continue
            topics[(asset_name, stream_name)] = resolve(raw_topic, asset_name, stream_name)
    return topics


@app.stream
async def on_data(msg: AssetDataMessage) -> None:
    """Buffer every incoming asset data message (number/string/boolean) from a mapped stream."""
    if (msg.resource.asset, msg.resource.data_stream) not in _topics:
        return      # unmapped stream (no topic configured): never buffered, warned at connect
    await store.append(msg.timestamp, msg.resource.asset, msg.resource.data_stream, msg.payload)


@app.task
async def export() -> None:
    """SDK-managed drain: started after on_connect, cancelled+awaited on disconnect."""
    if _settings is None or _writer is None:     # on_connect runs first (SDK lifecycle)
        raise RuntimeError("export task started before on_connect")  # explicit raise survives python -O
    await drain(_writer, store, _settings.upload, _settings.buffer, app.clock)


@app.on_connect
async def on_connect() -> None:
    """Validate config upfront, build topic map + buffer + writer before tasks run.
    Idempotent: store.setup() reuses an open buffer, so a re-fire won't leak."""
    global _settings, _writer, _topics
    try:
        settings = Settings(**app.app_configuration)
    except ValidationError as e:
        # errors() without input: str(e) would embed the raw value (a credential) in the log.
        logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
        raise
    _settings = settings
    logger.info("Exporter configured", **settings.upload.model_dump(exclude={"retry"}),
                retry_attempts=settings.upload.retry.attempts,
                max_backlog=settings.buffer.max_backlog)
    _topics = build_topic_map(app.assets)
    if not _topics:
        logger.warning("No streams mapped; check per-stream IO configuration (topic field)")
    await store.setup()
    if _writer is not None:                      # re-fire: drop the stale writer before rebuilding
        await _writer.teardown()
    _writer = KafkaWriter(settings.kafka, _topics)
    await _writer.setup()


@app.on_disconnect
async def on_disconnect() -> None:
    """Symmetric teardown: main owns the writer + buffer lifecycle (the drain task doesn't)."""
    if _writer is not None:
        await _writer.teardown()
    await store.teardown()


if __name__ == "__main__":
    app.run()
