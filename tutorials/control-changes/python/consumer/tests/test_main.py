import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import ControlChangeAck, Number, StateEnum
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


class TestSpeedReceiver:
    """Validate simulated command bounds and distinguish control inputs from readings."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asset", ["motor-01", "motor-02"])
    @pytest.mark.parametrize(
        "value,state",
        [
            (-1, StateEnum.rejected),
            (0, StateEnum.applied),
            (1000, StateEnum.applied),
            (1500, StateEnum.applied),
            (1501, StateEnum.rejected),
        ],
    )
    async def test_acknowledgement(
        self, asset: str, value: float, state: StateEnum
    ) -> None:
        """Acknowledge the control-interface Number on its original resource."""
        manifest = (
            ManifestBuilder.from_app_yaml()
            .add_asset("motor-01")
            .add_asset("motor-02")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream(asset, "motor_speed_setpoint"),
                    payload=value,
                )
            )
            await harness.run_until_idle()
        assert len(harness.outputs) == 1
        ack = harness.outputs[0]
        assert isinstance(ack, ControlChangeAck)
        assert ack.resource == KRNAssetDataStream(asset, "motor_speed_setpoint")
        assert ack.payload.state == state
        assert ack.payload.message == (
            "Simulated speed accepted"
            if state == StateEnum.applied
            else "Speed must be between 0 and 1500"
        )

    @pytest.mark.asyncio
    async def test_normal_reading_is_not_acknowledged(self) -> None:
        """A Number on a normal input doesn't invoke on_control_change."""
        manifest = ManifestBuilder.from_app_yaml().add_asset("motor-01").build()
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("motor-01", "temperature_c"), payload=25
                )
            )
            await harness.run_until_idle()
        assert harness.outputs == []
