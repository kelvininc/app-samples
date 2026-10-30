"""Verify conversion, routing, and timestamps through KelvinAppTest."""

from pathlib import Path

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


class TestConvertTemperature:
    """Exercise the registered handler with representative motor readings."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("celsius", "fahrenheit"), [(0, 32), (25, 77), (-40, -40), (100, 212)]
    )
    async def test_converts_reading(self, celsius: float, fahrenheit: float) -> None:
        """Publish the expected value on the same Asset and timestamp."""
        manifest = (
            ManifestBuilder.from_app_yaml(Path("app.yaml"))
            .add_asset("motor-02")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            timestamp = harness.clock.now()
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("motor-02", "temperature_c"),
                    payload=celsius,
                    timestamp=timestamp,
                )
            )
            await harness.run_until_idle()
            assert len(harness.outputs) == 1
            output = harness.outputs[0]
            assert isinstance(output, Number)
            assert str(output.resource) == "krn:ad:motor-02/temperature_f"
            assert output.payload == pytest.approx(fahrenheit)
            assert output.timestamp == timestamp
