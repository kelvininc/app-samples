"""Publish mapped numeric Kelvin readings as JSON records in Kafka."""

import asyncio
import json
from datetime import timezone

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import AssetDataMessage

app = KelvinApp()
producer: AIOKafkaProducer | None = None


@app.on_connect
async def connect_kafka() -> None:
    """Start one producer after Kelvin has delivered the runtime configuration."""
    global producer
    producer = AIOKafkaProducer(
        bootstrap_servers=app.app_configuration["bootstrap_servers"],
        acks="all",
        request_timeout_ms=10000,
    )
    try:
        await producer.start()
    except (asyncio.CancelledError, KafkaError, OSError, TimeoutError):
        await producer.stop()
        producer = None
        raise


@app.stream
async def export_reading(message: AssetDataMessage) -> None:
    """Send a mapped reading to its configured topic and await broker acknowledgement."""
    stream = app.assets[message.resource.asset].datastreams[
        message.resource.data_stream
    ]
    record = {
        "timestamp": message.timestamp.astimezone(timezone.utc).isoformat(),
        "asset": message.resource.asset,
        "datastream": stream.datastream.name,
        "payload": message.payload,
    }
    try:
        value = json.dumps(record, allow_nan=False).encode("utf-8")
    except ValueError:
        logger.warning(
            "invalid_numeric_reading",
            asset=message.resource.asset,
            datastream=stream.datastream.name,
        )
        return
    if producer is None:
        raise RuntimeError("Kafka producer has not connected")
    key = f"{message.resource.asset}/{stream.datastream.name}".encode()
    await producer.send_and_wait(stream.configuration["topic"], value, key=key)


@app.on_disconnect
async def disconnect_kafka() -> None:
    """Close the producer when the SDK disconnects."""
    global producer
    if producer is not None:
        await producer.stop()
        producer = None


if __name__ == "__main__":
    app.run()
