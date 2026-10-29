import json

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


def events(output: str, name: str) -> list[dict]:
    """Read structured events without depending on JSON field order."""
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [record for record in records if record.get("event") == name]


from kelvin.message import Boolean
from kelvin.message.base_messages import ParametersMsg


class TestParameterPublisher:
    """Publish a scoped parameter update on an explicit reset signal."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("asset", ["motor-01", "motor-02"])
    @pytest.mark.parametrize("reset", [False, True])
    async def test_reset_signal(self, asset: str, reset: bool) -> None:
        """A false signal changes nothing; a true signal targets its Asset."""
        manifest = (
            ManifestBuilder.from_app_yaml()
            .add_asset(asset, parameters={"temperature_limit": 95})
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                Boolean(
                    resource=KRNAssetDataStream(asset, "reset_temperature_limit"),
                    payload=reset,
                )
            )
            await harness.run_until_idle()
        assert len(harness.outputs) == int(reset)
        if reset:
            result = harness.outputs[0]
            assert isinstance(result, ParametersMsg)
            change = result.payload.resource_parameters[0]
            assert change.resource.asset == asset
            assert change.parameters[0].name == "temperature_limit"
            assert change.parameters[0].value == 80
