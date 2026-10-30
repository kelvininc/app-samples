"""Exercise the actual election client and registered App tasks with a fake HTTP API.

KelvinAppTest advances the publishing timer virtually. The election client uses real
asyncio time and a short 90ms lease; events synchronize its responses without test sleeps.
"""

import asyncio
import json
from pathlib import Path

import httpx
import main
import pytest
from kelvin.ha import AsyncLeaderElection
from kelvin.message import Number
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest, ManifestBuilder


class FakeLeaderAPI:
    """Grant or deny a lease, or simulate an unreachable Leader API."""

    def __init__(self, grants: bool = True) -> None:
        """Initialize the response mode and first-check-in event."""
        self.grants = grants
        self.unreachable = False
        self.checked = asyncio.Event()
        self.promoted = asyncio.Event()
        self.demoted = asyncio.Event()
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Respond at the network boundary used by the real SDK client."""
        assert request.url.path == "/leader/elect"
        body = json.loads(request.content)
        self.requests.append(body)
        self.checked.set()
        if self.unreachable:
            raise httpx.ConnectError("Leader API unreachable", request=request)
        return httpx.Response(
            200,
            json={
                "lease_duration": "90ms",
                "resources": [
                    {
                        "resource": body["resources"][0],
                        "leader": self.grants,
                        "current_leader": body["id"]
                        if self.grants
                        else "krn:wl:test-cluster/heartbeat-02",
                    }
                ],
            },
        )


def install_client(
    monkeypatch: pytest.MonkeyPatch, api: FakeLeaderAPI
) -> AsyncLeaderElection:
    """Use an HTTP-only fake while keeping election and App behavior real."""
    client = AsyncLeaderElection(transport=httpx.MockTransport(api))

    @client.on_promoted
    async def promoted() -> None:
        """Run the App's promotion hook and signal the test."""
        await main.on_promoted()
        api.promoted.set()

    @client.on_demoted
    async def demoted() -> None:
        """Run the App's demotion hook and signal the test."""
        await main.on_demoted()
        api.demoted.set()

    monkeypatch.setattr(main, "leader", client)
    return client


def manifest() -> RuntimeManifest:
    """Load the declared output and add a single test motor."""
    return ManifestBuilder.from_app_yaml(Path("app.yaml")).add_asset("motor-01").build()


class TestPublishHeartbeat:
    """Verify the timer's publishing decision through KelvinAppTest."""

    @pytest.mark.asyncio
    async def test_leader_publishes(
        self, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Publish a Number to the declared output after promotion."""
        api = FakeLeaderAPI()
        install_client(monkeypatch, api)
        async with KelvinAppTest(main.app, manifest=manifest()) as harness:
            await asyncio.wait_for(api.promoted.wait(), timeout=2)
            await harness.run_until_idle(timeout=6)
            assert len(harness.outputs) == 1
            output = harness.outputs[0]
            assert isinstance(output, Number)
            assert output.resource.asset == "motor-01"
            assert output.resource.data_stream == "publisher_heartbeat"
            assert output.payload == 1.0
        assert "publisher_promoted" in capfd.readouterr().out

    @pytest.mark.asyncio
    async def test_follower_skips_publish(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep the App running without publishing while another replica leads."""
        api = FakeLeaderAPI(grants=False)
        client = install_client(monkeypatch, api)
        async with KelvinAppTest(main.app, manifest=manifest()) as harness:
            await asyncio.wait_for(api.checked.wait(), timeout=2)
            await harness.run_until_idle(timeout=6)
            assert not client.is_leader
            assert client.current_leader == "krn:wl:test-cluster/heartbeat-02"
            assert harness.outputs == []

    @pytest.mark.asyncio
    async def test_expired_lease_stops_publish(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stop publishing after an unreachable API lets the lease expire."""
        api = FakeLeaderAPI()
        client = install_client(monkeypatch, api)
        async with KelvinAppTest(main.app, manifest=manifest()) as harness:
            await asyncio.wait_for(api.promoted.wait(), timeout=2)
            await harness.run_until_idle(timeout=6)
            count = len(harness.outputs)
            assert count > 0
            api.unreachable = True
            await asyncio.wait_for(api.demoted.wait(), timeout=2)
            assert not client.is_leader
            await harness.run_until_idle(timeout=6)
            assert len(harness.outputs) == count


class TestMaintainElection:
    """Verify SDK task cancellation stops check-ins and demotes locally."""

    @pytest.mark.asyncio
    async def test_shutdown_demotes(
        self, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Run the demotion hook on harness shutdown and retain the last leader ID."""
        api = FakeLeaderAPI()
        client = install_client(monkeypatch, api)
        async with KelvinAppTest(main.app, manifest=manifest()):
            await asyncio.wait_for(api.promoted.wait(), timeout=2)
            assert client.is_leader
        assert not client.is_leader
        assert api.demoted.is_set()
        assert client.current_leader == "krn:wl:test-cluster/heartbeat-01"
        assert all(
            body
            == {
                "id": "krn:wl:test-cluster/heartbeat-01",
                "resources": ["krn:app:elected-heartbeat"],
            }
            for body in api.requests
        )
        assert "publisher_demoted" in capfd.readouterr().out
