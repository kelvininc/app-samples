"""Publish simulated sensor readings to an external MQTT broker."""

import argparse
import asyncio
from itertools import cycle

import aiomqtt


async def simulate(host: str, port: int) -> None:
    """Publish a repeating temperature sequence once per second until cancelled."""
    async with aiomqtt.Client(hostname=host, port=port) as client:
        for value in cycle((24.5, 25.0, 25.5)):
            await client.publish("demo/motor/temperature", str(value))
            await asyncio.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="MQTT broker hostname")
    parser.add_argument("--port", type=int, default=1883)
    arguments = parser.parse_args()
    asyncio.run(simulate(arguments.host, arguments.port))
