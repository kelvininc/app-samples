import asyncio
import json
import aiomqtt
from pydantic.v1 import BaseSettings, ValidationError

from kelvin.application import KelvinApp, ResourceDatastream, AssetInfo
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import KMessageTypeData, Message, KMessageTypePrimitive
from kelvin.message.msg_type import PrimitiveTypes


class MQTTConfig(BaseSettings):
    ip: str = "test.mosquitto.org"
    port: int = 1883

class MQTTImporterApplication:
    def __init__(self, app: KelvinApp = KelvinApp()) -> None:
        self.app = app
        self.app.on_connect = self.on_connect
        self.app.on_disconnect = self.on_disconnect
        self.app.on_app_configuration = self.on_app_configuration
        self.config = MQTTConfig()
        self.io_map: dict[str, ResourceDatastream] = {}

    async def on_connect(self) -> None:
        logger.debug("App connected")

    async def on_disconnect(self) -> None:
        logger.debug("App disconnected")

    async def on_app_configuration(self, conf: dict) -> None:
        logger.debug("App configuration change", config=conf)

    def parse_mqtt_message(self, message: aiomqtt.Message) -> list[Message]:
        topic = message.topic.value
        logger.debug("Received message", topic=topic, payload=message.payload)

        asset, datastream, msg_type = self.get_resources_from_topic(topic)

        if isinstance(message.payload, bytes):
            payload = message.payload.decode("utf-8")
        else:
            payload = message.payload

        try:
            if msg_type.primitive == PrimitiveTypes.object:
                payload = json.loads(payload)

                msg = Message(
                    resource=KRNAssetDataStream(asset, datastream),
                    type=KMessageTypeData(primitive="object", icd=datastream),
                    payload=payload,
                )
            else:
                if msg_type.primitive == PrimitiveTypes.number:
                    payload = float(payload)
                if msg_type.primitive == PrimitiveTypes.string:
                    payload = str(payload)
                if msg_type.primitive == PrimitiveTypes.boolean:
                    payload = bool(payload)
                
            msg = Message(
                resource=KRNAssetDataStream(asset, datastream),
                type=msg_type,
                payload=payload,
            )
        except ValueError:
            logger.exception("Invalid message type", msg_type=msg_type, payload=payload)

            return []

        return [msg]

    async def read_task(self) -> None:
        logger.info("Starting read task")

        while True:
            client = aiomqtt.Client(
                hostname=self.config.ip,
                port=self.config.port
            )

            try:
                async with client:
                    logger.info("Connected to MQTT")

                    for topic in self.io_map.keys():
                        logger.debug("Subscribing to topic", topic=topic)
                        await client.subscribe(topic)
                    
                    async for message in client.messages:
                        messages = self.parse_mqtt_message(message)

                        for msg in messages:
                            await self.app.publish(msg)

                            logger.debug("Published message", msg=msg)
            except aiomqtt.MqttError:
                logger.exception("MQTT error")

                await asyncio.sleep(10)
            except Exception:
                logger.exception("Unexpected error")

                await asyncio.sleep(10)

    def get_resources_from_topic(self, topic: str) -> str | None:
        resource = self.io_map.get(topic)

        if resource is None:
            return None

        return resource.asset.asset, resource.datastream.name, resource.datastream.type
    
    def setup_io_map(self, assets: list[AssetInfo]) -> dict[str, ResourceDatastream]:
        io_map = {}

        for asset in assets:
            for datastream in asset.datastreams.values():
                try:
                    topic = datastream.configuration["topic"]
                except KeyError:
                    continue
                io_map[topic] = datastream

        return io_map
    
    async def main(self) -> None:
        await self.app.connect()

        try:
            self.config = MQTTConfig.parse_obj(self.app.app_configuration)
            self.io_map = self.setup_io_map(self.app.assets.values())
        except ValidationError:
            logger.exeption("Invalid configuration")

            exit(1)

        tasks = [self.read_task()]

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    app = MQTTImporterApplication()
    asyncio.run(app.main())
