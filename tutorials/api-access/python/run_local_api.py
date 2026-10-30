"""Run the App against a local Publisher with a prompted API access token."""

import asyncio
from getpass import getpass

from kelvin.api.client import AsyncClient
from main import create_app


async def run_local(url: str, access_token: str) -> None:
    """Keep the API client and normal App lifecycle on the same event loop."""
    async with AsyncClient(url=url, access_token=access_token) as client:
        await create_app(api=client).run_forever()


if __name__ == "__main__":
    url = input("Kelvin Cloud URL: ").strip()
    token = getpass("Kelvin access token: ")
    try:
        asyncio.run(run_local(url, token))
    except KeyboardInterrupt:
        pass
