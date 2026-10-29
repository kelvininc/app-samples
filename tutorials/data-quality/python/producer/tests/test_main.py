import json

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


def events(output: str, name: str) -> list[dict]:
    """Read structured events without depending on JSON field order."""
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [record for record in records if record.get("event") == name]


from kelvin.krn import KRNAssetDataStreamDataQuality


class TestQualityPublisher:
    """Assert the metric identity, inclusive boundaries and original time."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asset", ["motor-01", "motor-02"])
    @pytest.mark.parametrize(
        "temperature,score", [(-1, 0), (0, 100), (80, 100), (81, 0)]
    )
    async def test_range_score(
        self, asset: str, temperature: float, score: int
    ) -> None:
        """Publish one custom metric result per selected reading."""
        manifest = ManifestBuilder.from_app_yaml().add_asset(asset).build()
        async with KelvinAppTest(app, manifest=manifest) as harness:
            timestamp = harness.clock.now()
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream(asset, "temperature_c"),
                    payload=temperature,
                    timestamp=timestamp,
                )
            )
            await harness.run_until_idle()
        assert len(harness.outputs) == 1
        result = harness.outputs[0]
        assert isinstance(result, Number)
        assert result.resource == KRNAssetDataStreamDataQuality(
            asset, "temperature_c", "temperature_range"
        )
        assert result.payload == score
        assert result.timestamp == timestamp
