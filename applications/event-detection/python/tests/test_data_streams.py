"""Exercise below, boundary, and above-threshold data inputs."""

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number, RecommendationMsg
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest
from main import app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "temperature,expected_count", [(45.0, 0), (80.0, 0), (81.0, 1)]
)
async def test_temperature_boundary(
    motor_manifest: RuntimeManifest, temperature: float, expected_count: int
) -> None:
    """Produce a speed Recommendation only above the configured threshold."""
    async with KelvinAppTest(app, manifest=motor_manifest) as harness:
        await harness.publish(
            Number(
                resource=KRNAssetDataStream("motor-01", "temperature_c"),
                payload=temperature,  # Below, exactly at, or above the threshold of 80.
            )
        )
        await harness.run_until_idle()
        recommendations = [
            message
            for message in harness.outputs
            if isinstance(message, RecommendationMsg)
        ]
        assert len(recommendations) == expected_count
        if recommendations:
            change = recommendations[0].payload.actions.control_changes[0]
            assert change.resource.asset == "motor-01"
            assert change.resource.data_stream == "motor_speed_setpoint"
            assert change.payload == 1000
