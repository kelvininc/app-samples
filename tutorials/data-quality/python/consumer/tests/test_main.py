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


from kelvin.krn import KRNAssetDataQuality, KRNAssetDataStreamDataQuality


class TestQualityReader:
    """Distinguish stream quality, Asset quality and ordinary readings."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope", ["asset", "stream"])
    async def test_quality_scope(
        self, scope: str, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Read either scope without assuming every resource has a stream."""
        manifest = ManifestBuilder.from_app_yaml().add_asset("motor-01").build()
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.run_until_idle()
            resource = (
                KRNAssetDataQuality("motor-01", "motor_quality")
                if scope == "asset"
                else KRNAssetDataStreamDataQuality(
                    "motor-01", "temperature_c", "temperature_range"
                )
            )
            await harness.publish(Number(resource=resource, payload=75))
            await harness.run_until_idle()
        output = capfd.readouterr().out
        assert "motor_quality_received" in output and "motor-01" in output
        assert ("motor_quality" if scope == "asset" else "temperature_range") in output
        assert events(output, "motor_quality_received")[0]["data_stream"] == (
            None if scope == "asset" else "temperature_c"
        )
        assert "75" in output

    @pytest.mark.asyncio
    async def test_ignores_readings(self, capfd: pytest.CaptureFixture[str]) -> None:
        """An ordinary numeric reading isn't a quality result."""
        manifest = ManifestBuilder.from_app_yaml().add_asset("motor-01").build()
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.run_until_idle()
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("motor-01", "temperature_c"), payload=75
                )
            )
            await harness.run_until_idle()
        assert "motor_quality_received" not in capfd.readouterr().out
