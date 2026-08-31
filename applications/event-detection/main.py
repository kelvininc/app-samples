from datetime import timedelta

from kelvin.application import KelvinApp
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import AssetDataMessage, ControlChange, Recommendation

app = KelvinApp()


@app.stream(inputs=["motor_temperature"])
async def on_motor_temperature(msg: AssetDataMessage) -> None:
    """Recommend a motor-speed decrease when temperature crosses the asset's threshold.

    The control change rides inside the Recommendation. `kelvin_closed_loop` decides whether
    the platform auto-accepts (applies) it or leaves it for an operator to approve.
    """
    asset = msg.resource.asset
    temperature = msg.payload
    params = app.assets[asset].parameters

    threshold = params["temperature_max_threshold"]
    if temperature <= threshold:
        return

    set_point = params["speed_decrease_set_point"]
    auto_accepted = bool(params.get("kelvin_closed_loop", False))
    logger.info(
        "Temperature over threshold; recommending speed decrease",
        asset=asset,
        temperature=temperature,
        threshold=threshold,
        set_point=set_point,
        auto_accepted=auto_accepted,
    )

    await app.publish(
        Recommendation(
            resource=KRNAsset(asset),
            type="decrease_speed",
            control_changes=[
                ControlChange(
                    resource=KRNAssetDataStream(asset, "motor_speed_set_point"),
                    payload=set_point,
                    expiration_date=timedelta(minutes=10),
                )
            ],
            expiration_date=timedelta(hours=1),
            auto_accepted=auto_accepted,
        )
    )


if __name__ == "__main__":
    app.run()
