"""Inject numeric control-input messages and inspect acknowledgements."""

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import ControlChangeAck, Number, StateEnum
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest
from main import app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setpoint,state", [(1000.0, StateEnum.applied), (2000.0, StateEnum.rejected)]
)
async def test_speed_command(
    motor_manifest: RuntimeManifest, setpoint: float, state: StateEnum
) -> None:
    """Exercise both branches of the local acknowledgement handler."""
    async with KelvinAppTest(app, manifest=motor_manifest) as harness:
        await harness.publish(
            Number(
                resource=KRNAssetDataStream("motor-01", "motor_speed_setpoint"),
                payload=setpoint,  # Number on an input-cc interface, not a ControlChange builder.
            )
        )
        await harness.run_until_idle()
        acknowledgements = [
            message
            for message in harness.outputs
            if isinstance(message, ControlChangeAck)
        ]
        assert len(acknowledgements) == 1
        assert acknowledgements[0].resource.data_stream == "motor_speed_setpoint"
        assert acknowledgements[0].payload.state == state
