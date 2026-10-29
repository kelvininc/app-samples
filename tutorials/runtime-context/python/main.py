"""Read shared configuration and each assigned Asset's settings."""

from typing import Any

import structlog
from kelvin.application import KelvinApp
from kelvin.application.client import AssetInfo

app = KelvinApp()
logger = structlog.get_logger()


def log_configuration(configuration: dict[str, Any]) -> None:
    """Report the current Workload-wide temperature limit."""
    logger.info(
        "motor_configuration",
        temperature_limit=configuration["processing"]["temperature_limit"],
    )


def log_limit(asset: AssetInfo) -> None:
    """Report the current per-Asset parameter without changing it."""
    logger.info(
        "motor_parameter",
        asset=asset.name,
        temperature_limit=asset.parameters["temperature_limit"],
    )


@app.on_connect
async def read_initial_context() -> None:
    """Read each setting after the initial runtime context arrives."""
    log_configuration(app.app_configuration)
    for asset in app.assets.values():
        log_limit(asset)
        logger.info(
            "motor_manufacturer",
            asset=asset.name,
            manufacturer=asset.properties.get("manufacturer", "unknown"),
        )


@app.on_app_configuration
async def read_updated_configuration(configuration: dict[str, Any]) -> None:
    """Report changed shared configuration supplied by the runtime."""
    log_configuration(configuration)


@app.on_asset_change
async def read_changed_parameters(new: AssetInfo | None, old: AssetInfo | None) -> None:
    """Report additions or changed parameters; ignore removals and unchanged values."""
    if new is not None and (old is None or new.parameters != old.parameters):
        log_limit(new)


if __name__ == "__main__":
    app.run()
