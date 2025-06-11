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
                type="Resnet Issue",
                title="Resnet Test Issue",
                expiration_date=datetime.now(timezone.utc) + timedelta(days=7),
                payload={
                    "type": "Task",
                    "name": "Resnet Test Issue",
                    "status": "TODO",
                    # "customFields": {"@system/dispatched": True, "flag": False},
                    "priority": 3,
                    "labels": [],
                    "estimatedStartDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                    "estimatedEndDate": (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                },
            )
            await asyncio.sleep(10)
