import structlog
from kelvin.api.client import AsyncClient
from kelvin.application import KelvinApp
from kelvin.application.api_client import UnavailableApiClientError

logger = structlog.get_logger()


def create_app(api: AsyncClient | None = None) -> KelvinApp:
    """Build the App with an optional client for local read-only integration."""
    app = KelvinApp(api=api)

    @app.task
    async def inspect_assets() -> None:
        """Read one Asset page once after normal App startup."""
        try:
            result = await app.api.asset.list_assets(fetch=False)
        except UnavailableApiClientError:
            logger.warning("kelvin_api_not_configured")
            return
        assets = result.data or []
        logger.info(
            "kelvin_asset_page",
            count=len(assets),
            names=[asset.name for asset in assets],
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run()
