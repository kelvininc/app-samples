from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetParameter
from kelvin.message import AppParameter, AppParameters, AssetDataMessage

app = KelvinApp()


@app.stream(inputs=["reset_temperature_limit"])
async def reset_limit(message: AssetDataMessage) -> None:
    """Request the current App's default limit only on an explicit reset."""
    if not message.payload:
        return
    await app.publish(
        AppParameters(
            parameters=[
                AppParameter(
                    resource=KRNAssetParameter(
                        message.resource.asset, "temperature_limit"
                    ),
                    value=80,
                    comment="Reset requested by the reset_temperature_limit input.",
                )
            ]
        )
    )


if __name__ == "__main__":
    app.run()
