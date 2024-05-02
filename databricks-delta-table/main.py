import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Dict, List

from delta_table_retriever import DeltaTableRetriever
from kelvin.application import KelvinApp
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message import ControlChange, Recommendation


async def process_predictions(app: KelvinApp, predictions: List[Dict[str, any]]):
    for prediction in predictions:
        # Get relevant info from prediction
        asset = prediction["asset"]
        recommended_speed = prediction["recommended_speed"]

        print(f"publishing recommendation for asset '{asset}' with recommended speed: {recommended_speed}")

        # Publish Kelvin Recommendation with a Control Change
        await app.publish(
            Recommendation(
                resource=KRNAsset(asset),
                type="change_speed",
                control_changes=[
                    ControlChange(
                        resource=KRNAssetDataStream(asset, "motor_speed_set_point"),
                        expiration_date=timedelta(hours=1),
                        payload=recommended_speed,
                    )
                ],
            )
        )


async def main() -> None:

    # Configure Data Bricks Delta Table Retriever
    server_hostname = os.getenv("DATABRICKS_SERVER_HOSTNAME")
    http_path = os.getenv("DATABRICKS_HTTP_PATH")
    access_token = os.getenv("DATABRICKS_ACCESS_TOKEN")

    delta_table_retriever = DeltaTableRetriever(server_hostname=server_hostname, http_path=http_path, access_token=access_token)

    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    # Define polling interval
    polling_interval = app.app_configuration.get("polling_interval", 30)

    while True:
        try:
            # Get predictions from Databricks Delta Table
            cutoff = datetime.now(UTC) - timedelta(seconds=polling_interval)
            predictions = await delta_table_retriever.query(cutoff=cutoff)

            # Process predictions
            await process_predictions(app=app, predictions=predictions)
        except Exception as ex:
            print("failed to get data from databricks delta table", ex)
        finally:
            await asyncio.sleep(polling_interval)


if __name__ == "__main__":
    asyncio.run(main())
