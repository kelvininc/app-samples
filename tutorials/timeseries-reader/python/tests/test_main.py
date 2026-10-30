import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number, String
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


class TestTemperatureReader:
    """Check input selection and source identity through the registered stream."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asset", ["motor-01", "motor-02"])
    async def test_selected_input(
        self, asset: str, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Receive the selected reading for either assigned Asset."""
        manifest = (
            ManifestBuilder.from_app_yaml()
            .add_asset("motor-01")
            .add_asset("motor-02")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream(asset, "temperature_c"), payload=21.5
                )
            )
            await harness.run_until_idle()
        events = [
            line
            for line in capfd.readouterr().out.splitlines()
            if "motor_temperature_received" in line
        ]
        assert len(events) == 1
        assert asset in events[0]
        assert "temperature_c" in events[0]
        assert "21.5" in events[0]
        assert harness.outputs == []

    @pytest.mark.asyncio
    async def test_other_input_is_ignored(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A declared String input doesn't invoke the temperature handler."""
        manifest = ManifestBuilder.from_app_yaml().add_asset("motor-01").build()
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                String(
                    resource=KRNAssetDataStream("motor-01", "motor_state"),
                    payload="running",
                )
            )
            await harness.run_until_idle()
        assert "motor_temperature_received" not in capfd.readouterr().out
