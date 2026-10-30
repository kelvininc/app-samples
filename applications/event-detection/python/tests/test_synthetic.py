"""Compare constant and cyclic signals with a finite sampling duration."""

from datetime import timedelta

import pytest
from kelvin.message import RecommendationMsg
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest
from kelvin.testing.sources import SyntheticSource
from kelvin.testing.sources.synthetic import ConstantWave, SineWave, WavePattern
from main import app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pattern,expected_count",
    [
        (ConstantWave(value=81), 4),
        (SineWave(amplitude=10, period=timedelta(seconds=4), offset=80), 1),
    ],
)
async def test_synthetic_patterns(
    motor_manifest: RuntimeManifest, pattern: WavePattern, expected_count: int
) -> None:
    """Sample four values and assert which ones cross the threshold."""
    source = SyntheticSource(
        pattern=pattern,
        datastream="temperature_c",
        sample_rate=timedelta(seconds=1),
        duration=timedelta(
            seconds=4
        ),  # Finite source: samples at seconds 0, 1, 2, and 3.
    ).with_asset("motor-01")
    async with KelvinAppTest(app, manifest=motor_manifest).add_source(
        source
    ) as harness:
        await harness.run_until_idle(timeout=5)
        recommendations = [
            message
            for message in harness.outputs
            if isinstance(message, RecommendationMsg)
        ]
        assert len(harness.inputs) == 4
        assert len(recommendations) == expected_count
