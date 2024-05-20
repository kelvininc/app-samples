from __future__ import annotations

import asyncio
from typing import Optional

import yaml
from pydantic import BaseModel

from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import Boolean, Message, Number, String
from kelvin.krn import KRNAssetDataStream
from kelvin.publisher.publisher import AppConfig, CSVPublisher, MessageData

CONFIG_FILE_PATH = "app.yaml"
CSV_FILE_PATH = "csv/data.csv"


def message_from_message_data(data: MessageData, outputs: list) -> Optional[Message]:
    output = next((output for output in outputs if output["name"] == data.resource), None)
    if output is None:
        logger.error("CSV Datastream not found in the app.yaml outputs", metric=data.resource)
        return None

    data_type = output.get("data_type")
    if data_type == "boolean":
        msg_type = Boolean
    elif data_type == "number":
        msg_type = Number
    elif data_type == "string":
        msg_type = String
    else:
        return None

    return msg_type(resource=KRNAssetDataStream(data.asset, data.resource), payload=data.value)


async def main() -> None:
    with open(CONFIG_FILE_PATH) as f:
        config_yaml = yaml.safe_load(f)
        config = AppConfig.parse_obj(config_yaml)
        outputs = config.app.kelvin.outputs

    app = KelvinApp()
    await app.connect()

    assets = list(app.assets.keys())

    # Replay CSV Ingestion after reaching end of file
    replay = app.app_configuration.get("replay", True)
    # CSV Row Publish Rate (in seconds)
    publish_rate = app.app_configuration.get("publish_rate", 30)

    publisher = CSVPublisher(CSV_FILE_PATH, publish_rate)

    while True:
        logger.info("Initializing CSV Ingestion")

        async for data in publisher.run():
            for asset in assets:
                data.asset = asset
                msg = message_from_message_data(data, outputs)
                if msg is not None:
                    logger.info(f"Publishing Datastream: {msg}")

                    await app.publish(msg)

        if not replay:
            return


if __name__ == "__main__":
    asyncio.run(main())
