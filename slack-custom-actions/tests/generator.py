# mygenerator.py
import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

from kelvin.krn import KRNAsset
from kelvin.logs import logger
from kelvin.message import CustomAction, Message
from kelvin.publisher import DataGenerator


class CustomActionGenerator(DataGenerator):

    def __init__(self) -> None:
        logger.info("Initializing CustomActionGenerator")

    async def run(self) -> AsyncGenerator[Message, None]:
        logger.info("Running MyGenerator")

        for i in range(10):
            yield CustomAction(
                resource=KRNAsset("test-asset-1"),
                type="Slack Message",
                title="Slack Test Message",
                expiration_date=datetime.now(timezone.utc) + timedelta(days=7),
                payload={
                    "message": {
                        "text": "Test message from Kelvin Slack Message Action Generator",
                    },
                    "channel": "sales-demo-notifications"
                },
            )
            await asyncio.sleep(10)
