import json

import pytest
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


def events(output: str, name: str) -> list[dict]:
    """Read structured events without depending on JSON field order."""
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [record for record in records if record.get("event") == name]


from types import SimpleNamespace
from unittest.mock import AsyncMock

from kelvin.application.api_client import initialize_api_client


class TestApiReader:
    """Replace the external API boundary while keeping SDK task startup real."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("names", [None, [], ["motor-01", "motor-02"]])
    async def test_reads_one_page(
        self,
        names: list[str] | None,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """The task requests a single page and logs the returned names."""
        request = AsyncMock(
            return_value=SimpleNamespace(
                data=None
                if names is None
                else [SimpleNamespace(name=name) for name in names]
            )
        )
        monkeypatch.setattr(
            app, "_api", SimpleNamespace(asset=SimpleNamespace(list_assets=request))
        )
        async with KelvinAppTest(
            app, manifest=ManifestBuilder.from_app_yaml().build()
        ) as harness:
            await harness.run_until_idle()
        request.assert_awaited_once_with(fetch=False)
        output = capfd.readouterr().out
        assert events(output, "kelvin_asset_page")[0]["count"] == len(names or [])
        assert all(name in output for name in names or [])

    @pytest.mark.asyncio
    async def test_missing_api_configuration(
        self, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """An unconfigured client yields a specific startup warning."""
        monkeypatch.delenv("KELVIN_CLIENT__URL", raising=False)
        monkeypatch.setattr(app, "_api", initialize_api_client())
        async with KelvinAppTest(
            app, manifest=ManifestBuilder.from_app_yaml().build()
        ) as harness:
            await harness.run_until_idle()
        assert "kelvin_api_not_configured" in capfd.readouterr().out
