import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Boolean, Number, String
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


class TestTelemetryPublisher:
    """Check all output types, source timestamps and Asset routing."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asset", ["motor-01", "motor-02"])
    @pytest.mark.parametrize(
        "celsius,fahrenheit,high",
        [(-40, -40, False), (0, 32, False), (80, 176, False), (81, 177.8, True)],
    )
    async def test_outputs(
        self, asset: str, celsius: float, fahrenheit: float, high: bool
    ) -> None:
        """Preserve the source Asset/time and match each declared primitive."""
        manifest = (
            ManifestBuilder.from_app_yaml()
            .add_asset("motor-01")
            .add_asset("motor-02")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            timestamp = harness.clock.now()
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream(asset, "temperature_c"),
                    payload=celsius,
                    timestamp=timestamp,
                )
            )
            await harness.run_until_idle()
        assert len(harness.outputs) == 4
        outputs = {m.resource.data_stream: m for m in harness.outputs}
        assert isinstance(outputs["temperature_f"], Number)
        assert outputs["temperature_f"].payload == pytest.approx(fahrenheit)
        assert isinstance(outputs["temperature_alarm"], Boolean)
        assert outputs["temperature_alarm"].payload is high
        assert isinstance(outputs["temperature_status"], String)
        assert outputs["temperature_status"].payload == ("high" if high else "normal")
        assert outputs["temperature_summary"].type.primitive == "object"
        assert outputs["temperature_summary"].payload == {
            "celsius": celsius,
            "fahrenheit": pytest.approx(fahrenheit),
        }
        assert all(
            m.resource.asset == asset and m.timestamp == timestamp
            for m in harness.outputs
        )

    @pytest.mark.asyncio
    async def test_other_input_is_ignored(self) -> None:
        """Unselected inputs must not create any of the four outputs."""
        manifest = ManifestBuilder.from_app_yaml().add_asset("motor-01").build()
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                String(
                    resource=KRNAssetDataStream("motor-01", "motor_state"),
                    payload="running",
                )
            )
            await harness.run_until_idle()
        assert harness.outputs == []
