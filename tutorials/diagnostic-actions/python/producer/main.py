from datetime import timedelta
from uuid import uuid4

import structlog
from kelvin.application import KelvinApp
from kelvin.krn import KRNAsset
from kelvin.message import AssetDataMessage, CustomAction, CustomActionReport

app = KelvinApp()
logger = structlog.get_logger()


@app.stream(inputs=["temperature_c"])
async def request_diagnostics(message: AssetDataMessage) -> None:
    """Request read-only diagnostics for an above-threshold reading."""
    if message.payload <= 80:
        return
    request_id = uuid4()
    await app.publish(
        CustomAction(
            resource=KRNAsset(message.resource.asset),
            type="run_diagnostics",
            title="Inspect motor temperature",
            payload={"include_history": True},
            expiration_date=timedelta(seconds=30),
            custom_action_id=request_id,
        )
    )
    logger.info(
        "diagnostics_requested", action_id=str(request_id), asset=message.resource.asset
    )


@app.on_custom_action_report
async def receive_report(report: CustomActionReport) -> None:
    """Record the final state and correlate it with the original request."""
    logger.info(
        "diagnostics_report",
        action_id=str(report.action_id),
        state=report.state.value,
        response=report.response,
        failure_reason=report.failure_reason,
    )


if __name__ == "__main__":
    app.run()
