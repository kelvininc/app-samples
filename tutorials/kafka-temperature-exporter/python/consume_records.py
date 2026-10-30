"""Read exported records with a separate Kafka client."""

import argparse
import asyncio

from aiokafka import AIOKafkaConsumer


async def consume(bootstrap_servers: str, topic: str, count: int) -> None:
    """Print the requested number of records, then close the consumer."""
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        auto_offset_reset="earliest",
        group_id=None,
    )
    await consumer.start()
    try:
        for _ in range(count):
            record = await asyncio.wait_for(consumer.getone(), timeout=30)
            print(record.value.decode("utf-8"), flush=True)
    finally:
        await consumer.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bootstrap_servers")
    parser.add_argument("topic")
    parser.add_argument("--count", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.count < 1:
        parser.error("--count must be at least 1")
    asyncio.run(consume(arguments.bootstrap_servers, arguments.topic, arguments.count))
