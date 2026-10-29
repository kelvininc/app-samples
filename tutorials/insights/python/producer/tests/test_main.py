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


from kelvin.message import DataTagMsg


class TestInsightPublisher:
    """Check threshold, source identity, contextual stream and event time."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asset", ["motor-01", "motor-02"])
    @pytest.mark.parametrize("temperature", [79, 80, 81])
    async def test_labels_high_reading(self, asset: str, temperature: float) -> None:
        """Publish only above the limit, using the input's Asset and time."""
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
        assert len(harness.outputs) == int(temperature > 80)
        if temperature > 80:
            event = harness.outputs[0]
            assert isinstance(event, DataTagMsg)
            assert event.resource.asset == asset
            assert event.payload.tag_name == "temperature_high"
            assert event.payload.start_date == timestamp == event.payload.end_date
            assert [str(resource) for resource in event.payload.contexts] == [
                "krn:datastream:temperature_c"
            ]
