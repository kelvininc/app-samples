"""MQTT protocol adapter: publishes fleet readings to an MQTT broker each tick."""

import asyncio
import ssl
from datetime import datetime, timezone

import aiomqtt
from kelvin.logs import logger

from fleet import Fleet
from messages import build_messages
from settings import Settings


class MqttPublisher:
    """Connects to an MQTT broker and publishes the fleet's readings every tick.

    Parameters:
        settings: The validated simulator configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fleet = Fleet(settings.assets, settings.simulation.seed)

    def _client(self) -> aiomqtt.Client:
        mqtt = self._settings.mqtt
        password = mqtt.auth.password.get_secret_value() if mqtt.auth.password else None
        return aiomqtt.Client(
            hostname=mqtt.host,
            port=mqtt.port,
            identifier=mqtt.client_id,
            username=mqtt.auth.username,
            password=password,
            tls_context=ssl.create_default_context() if mqtt.use_tls else None,
        )

    async def run(self) -> None:
        """Connect and publish forever at the configured tick rate."""
        mqtt = self._settings.mqtt
        logger.info(
            "Simulator starting",
            host=mqtt.host,
            port=mqtt.port,
            assets=self._fleet.asset_count,
            simulated_tags=len(self._fleet.simulated),
            payload=mqtt.publish.payload,
            tick=self._settings.simulation.tick,
        )
        async with self._client() as client:
            logger.info("Connected to broker", host=mqtt.host, topic=mqtt.publish.topic)
            await self._publish_loop(client)

    async def _publish_loop(self, client: aiomqtt.Client) -> None:
        pub = self._settings.mqtt.publish
        tick = self._settings.simulation.tick
        # Static tags (setpoints/commands) publish as constant telemetry: publishers
        # have no inbound path, so "writable" degrades to a steady value here.
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            while True:
                t = loop.time() - start
                now = datetime.now(timezone.utc)
                readings = list(self._fleet.sample(t, include_static=True))
                for msg in build_messages(readings, pub.payload, pub.timestamp, pub.topic, now):
                    await client.publish(msg.topic, payload=msg.payload, qos=pub.qos)
                await asyncio.sleep(tick)
        except Exception:
            # Fail fast (the platform restarts the workload), but say why first.
            logger.exception("Publish loop failed; shutting down")
            raise
