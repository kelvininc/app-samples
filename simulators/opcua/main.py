"""Entry point: load configuration, build the simulator, serve forever."""

import asyncio
import sys

from kelvin.logs import logger
from pydantic import ValidationError

from server import SimulatorServer
from settings import Settings


def load_settings() -> Settings:
    """Load and validate configuration (env vars > platform config.yaml > bundled app.yaml defaults).

    Returns:
        The validated Settings.

    Raises:
        SystemExit: If the configuration is invalid or defines no assets.
    """
    try:
        settings = Settings()
    except ValidationError as e:
        # errors() without input: str(e) would embed the raw value (a credential) in the log.
        logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
        raise SystemExit(1) from e

    # Edge case: an explicitly empty assets list simulates nothing; fail fast and visibly.
    if not settings.assets:
        logger.error("No assets configured; nothing to simulate")
        raise SystemExit(1)
    return settings


async def main() -> None:
    settings = load_settings()
    await SimulatorServer(settings).start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
