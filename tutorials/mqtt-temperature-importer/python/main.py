"""Import numeric temperature readings from an external MQTT broker."""

import asyncio
import math

import aiomqtt
from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import KMessageTypeData, Message

app = KelvinApp()


def decode_temperature(payload: bytes) -> float:
    """Decode a finite numeric payload; raise ValueError for invalid readings."""
    try:
        value = float(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Temperature must be a finite number") from error
    if not math.isfinite(value):
        raise ValueError("Temperature must be a finite number")
    return value


def topic_targets(application: KelvinApp) -> dict[str, list[KRNAssetDataStream]]:
    """Group runtime-mapped Asset Data Streams by their configured MQTT topic."""
    targets: dict[str, list[KRNAssetDataStream]] = {}
    for asset_name, asset in application.assets.items():
        for stream in asset.datastreams.values():
            topic = stream.configuration["topic"]
            targets.setdefault(topic, []).append(
                KRNAssetDataStream(asset_name, stream.datastream.name)
            )
    return targets


async def publish_reading(
    application: KelvinApp,
    targets: dict[str, list[KRNAssetDataStream]],
    topic: aiomqtt.Topic,
    payload: bytes,
) -> None:
    """Publish a valid reading to mapped targets; skip malformed numeric payloads."""
    try:
        value = decode_temperature(payload)
    except (ValueError, UnicodeDecodeError):
        logger.warning("invalid_temperature", topic=str(topic))
        return
    for topic_filter, resources in targets.items():
        if topic.matches(topic_filter):
            for resource in resources:
                await application.publish(
                    Message(
                        type=KMessageTypeData(primitive="number"),
                        resource=resource,
                        payload=value,
                    )
                )


async def ingest(application: KelvinApp) -> None:
    """Read the external broker until disconnected; propagate connection errors."""
    configuration = application.app_configuration
    targets = topic_targets(application)
    async with aiomqtt.Client(
        hostname=configuration["mqtt_host"], port=int(configuration["mqtt_port"])
    ) as client:
        for topic in list(targets):
            try:
                await client.subscribe(topic)
            except ValueError:
                logger.warning("invalid_mqtt_topic", topic=topic)
                del targets[topic]
        logger.info(
            "mqtt_connected", host=configuration["mqtt_host"], topics=len(targets)
        )
        async for message in client.messages:
            await publish_reading(
                application, targets, message.topic, bytes(message.payload)
            )
    raise aiomqtt.MqttError("MQTT message stream ended")


@app.task
async def read_temperatures() -> None:
    """Read MQTT in an SDK-managed task; reconnect after broker network failures."""
    while True:
        try:
            await ingest(app)
        except (aiomqtt.MqttError, OSError) as error:
            logger.warning("mqtt_disconnected", error=str(error))
            await asyncio.sleep(5)


if __name__ == "__main__":
    app.run()
