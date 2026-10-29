import structlog
from kelvin.application import KelvinApp
from kelvin.message import Insight

app = KelvinApp()
logger = structlog.get_logger()


@app.on_insight
async def receive_insight(insight: Insight) -> None:
    """Record the event identity and its platform acceptance state."""
    logger.info(
        "motor_insight_received",
        asset=insight.resource.asset,
        tag_name=insight.tag_name,
        acceptance=insight.acceptance,
    )


if __name__ == "__main__":
    app.run()
