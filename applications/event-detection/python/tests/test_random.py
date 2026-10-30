"""Generate a bounded, reproducible high-temperature sequence."""

from datetime import timedelta

import pytest
from kelvin.message import RecommendationMsg
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest
from kelvin.testing.sources import RandomSource
from main import app


@pytest.mark.asyncio
async def test_random_high_temperatures(motor_manifest: RuntimeManifest) -> None:
    """Recommend a speed decrease for each generated reading above 80."""
    source = (
        RandomSource(
            datastreams=["temperature_c"],
            min_value=81.0,
            max_value=90.0,  # All readings exceed the configured threshold.
            seed=17,
            count=4,  # Fixed sequence and finite message count.
        )
        .with_asset("motor-01")
        .with_timing(timedelta(seconds=1))
    )
    async with KelvinAppTest(app, manifest=motor_manifest).add_source(
        source
    ) as harness:
        await harness.run_until_idle(timeout=5)
        values = [message.payload for message in harness.inputs]
        recommendations = [
            message
            for message in harness.outputs
            if isinstance(message, RecommendationMsg)
        ]
        assert len(values) == 4
        assert all(81 <= value <= 90 for value in values)
        assert len(recommendations) == 4
