"""Check API-client cleanup when the local App run is cancelled."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import run_local_api
from kelvin.api.client import AsyncClient


@pytest.mark.asyncio
async def test_cancellation_closes_explicit_api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close the real HTTP client even when the App lifecycle is cancelled."""
    clients: list[AsyncClient] = []
    run_forever = AsyncMock(side_effect=asyncio.CancelledError)

    def create_app(api: AsyncClient) -> SimpleNamespace:
        """Capture the dependency while replacing the long-running App boundary."""
        clients.append(api)
        return SimpleNamespace(run_forever=run_forever)

    monkeypatch.setattr(run_local_api, "create_app", create_app)
    with pytest.raises(asyncio.CancelledError):
        await run_local_api.run_local("https://beta.kelvininc.com", "local-test-token")
    run_forever.assert_awaited_once()
    assert clients[0]._client.is_closed
