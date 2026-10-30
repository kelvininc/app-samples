import json

import pytest
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


def events(output: str, name: str) -> list[dict]:
    """Read structured events without depending on JSON field order."""
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [record for record in records if record.get("event") == name]


class TestParameterReader:
    """Read values per Asset and handle subsequent runtime changes."""

    @pytest.mark.asyncio
    async def test_asset_values_and_changes(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Keep two Assets independent and ignore an unchanged manifest."""
        builder = (
            ManifestBuilder.from_app_yaml()
            .add_asset("motor-01", parameters={"temperature_limit": 80})
            .add_asset("motor-02", parameters={"temperature_limit": 95})
        )
        async with KelvinAppTest(app, manifest=builder.build()) as harness:
            await harness.run_until_idle()
            initial = capfd.readouterr().out
            assert initial.count("motor_parameter") == 2
            assert {
                event["temperature_limit"]
                for event in events(initial, "motor_parameter")
            } == {80, 95}
            await harness.publish(builder.build())
            await harness.run_until_idle()
            assert "motor_parameter" not in capfd.readouterr().out
            builder.add_asset("motor-01", parameters={"temperature_limit": 85})
            await harness.publish(builder.build())
            await harness.run_until_idle()
            changed = capfd.readouterr().out
            assert changed.count("motor_parameter") == 1
            assert events(changed, "motor_parameter")[0]["asset"] == "motor-01"
            assert events(changed, "motor_parameter")[0]["temperature_limit"] == 85

    @pytest.mark.asyncio
    async def test_asset_removal(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A removed Asset doesn't cause a lookup failure or new value log."""
        builder = ManifestBuilder.from_app_yaml().add_asset(
            "motor-01", parameters={"temperature_limit": 80}
        )
        async with KelvinAppTest(app, manifest=builder.build()) as harness:
            await harness.run_until_idle()
            capfd.readouterr()
            await harness.publish(ManifestBuilder.from_app_yaml().build())
            await harness.run_until_idle()
            assert "motor_parameter" not in capfd.readouterr().out
