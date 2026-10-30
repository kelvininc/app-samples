import json

import pytest
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


def events(output: str, name: str) -> list[dict]:
    """Read structured events without depending on JSON field order."""
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [record for record in records if record.get("event") == name]


class TestConfigurationReader:
    """Read initial and changed values without duplicate startup callbacks."""

    @pytest.mark.asyncio
    async def test_initial_and_changed_values(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The second manifest replaces the current nested configuration."""
        builder = ManifestBuilder.from_app_yaml().add_asset(
            "motor-01", parameters={"temperature_limit": 80}
        )
        async with KelvinAppTest(app, manifest=builder.build()) as harness:
            await harness.run_until_idle()
            initial = capfd.readouterr().out
            assert initial.count("motor_configuration") == 1
            assert events(initial, "motor_configuration")[0]["temperature_limit"] == 80
            await harness.publish(
                builder.set_configuration(
                    {"processing": {"temperature_limit": 90}}
                ).build()
            )
            await harness.run_until_idle()
            changed = capfd.readouterr().out
            assert changed.count("motor_configuration") == 1
            assert events(changed, "motor_configuration")[0]["temperature_limit"] == 90
            await harness.publish(builder.build())
            await harness.run_until_idle()
            assert "motor_configuration" not in capfd.readouterr().out
