"""Kafka protocol adapter: produces fleet readings to Kafka topics each tick."""

import asyncio
import ssl
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaProducer
from aiokafka.helpers import create_ssl_context
from kelvin.logs import logger

from fleet import Fleet
from messages import build_messages
from settings import KafkaSecurity, Settings


def _ssl_context(security: KafkaSecurity) -> ssl.SSLContext:
    # PEM content (cadata/certdata/keydata), never file paths: the material arrives
    # through the platform config, not the container filesystem.
    tls = security.tls
    kwargs: dict[str, Any] = {"cadata": tls.ca_cert or None}
    if tls.client_cert:
        kwargs["certdata"] = tls.client_cert
        kwargs["keydata"] = tls.client_key.get_secret_value() if tls.client_key else None
    return create_ssl_context(**kwargs)


class KafkaPublisher:
    """Connects to Kafka and produces the fleet's readings every tick.

    Parameters:
        settings: The validated simulator configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fleet = Fleet(settings.assets, settings.simulation.seed)

    def _producer_kwargs(self) -> dict[str, Any]:
        kafka = self._settings.kafka
        sec = kafka.security
        kwargs: dict[str, Any] = {
            "bootstrap_servers": kafka.bootstrap_servers,
            "security_protocol": sec.protocol,
            "acks": 1,
        }
        if sec.protocol in ("SSL", "SASL_SSL"):
            kwargs["ssl_context"] = _ssl_context(sec)
        if sec.protocol.startswith("SASL"):
            kwargs["sasl_mechanism"] = sec.sasl.mechanism
            kwargs["sasl_plain_username"] = sec.sasl.username
            kwargs["sasl_plain_password"] = sec.sasl.password.get_secret_value() if sec.sasl.password else None
        return kwargs

    async def run(self) -> None:
        """Connect and produce forever at the configured tick rate."""
        kafka = self._settings.kafka
        logger.info(
            "Simulator starting",
            bootstrap_servers=kafka.bootstrap_servers,
            protocol=kafka.security.protocol,
            assets=self._fleet.asset_count,
            simulated_tags=len(self._fleet.simulated),
            payload=kafka.publish.payload,
            tick=self._settings.simulation.tick,
        )
        producer = AIOKafkaProducer(**self._producer_kwargs())
        await producer.start()
        logger.info("Connected to Kafka", topic=kafka.publish.topic)
        try:
            await self._publish_loop(producer)
        finally:
            await producer.stop()

    async def _publish_loop(self, producer: AIOKafkaProducer) -> None:
        pub = self._settings.kafka.publish
        tick = self._settings.simulation.tick
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            while True:
                t = loop.time() - start
                now = datetime.now(timezone.utc)
                # Static tags publish as constant telemetry (publishers have no inbound path).
                readings = list(self._fleet.sample(t, include_static=True))
                for msg in build_messages(readings, pub.payload, pub.timestamp, pub.topic, now, key_template=pub.key):
                    key = msg.key.encode() if msg.key else None
                    await producer.send(msg.topic, value=msg.payload, key=key)
                await asyncio.sleep(tick)
        except Exception:
            # Fail fast (the platform restarts the workload), but say why first.
            logger.exception("Publish loop failed; shutting down")
            raise
