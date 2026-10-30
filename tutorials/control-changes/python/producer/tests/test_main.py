from datetime import timedelta

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import ControlChangeMsg, Number
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


class TestSpeedPublisher:
    """Check trigger boundaries, destination, expiry and retry serialization."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asset", ["motor-01", "motor-02"])
    @pytest.mark.parametrize("temperature,expected", [(79, 0), (80, 0), (81, 1)])
    async def test_request(self, asset: str, temperature: float, expected: int) -> None:
        """Publish one request only above 80 with the intended protocol fields."""
        manifest = (
            ManifestBuilder.from_app_yaml()
            .add_asset("motor-01")
            .add_asset("motor-02")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            now = harness.clock.now()
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream(asset, "temperature_c"),
                    payload=temperature,
                )
            )
            await harness.run_until_idle()
        assert len(harness.outputs) == expected
        if expected:
            request = harness.outputs[0]
            assert isinstance(request, ControlChangeMsg)
            assert request.resource == KRNAssetDataStream(asset, "motor_speed_setpoint")
            assert request.payload.payload == 1000
            assert request.payload.expiration_date == now + timedelta(minutes=5)
            assert request.payload.retries == 0
            assert request.payload.timeout == 60
