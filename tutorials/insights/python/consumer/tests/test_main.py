import json

import pytest
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


def events(output: str, name: str) -> list[dict]:
    """Read structured events without depending on JSON field order."""
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [record for record in records if record.get("event") == name]


from kelvin.krn import KRNAsset
from kelvin.message import Insight


class TestInsightReader:
    """Exercise live Insight callback conversion and acceptance values."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("acceptance", [None, "accepted", "snoozed"])
    async def test_receives_event(
        self, acceptance: str | None, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Retain the event's Asset, type and optional acceptance state."""
        manifest = ManifestBuilder.from_app_yaml().add_asset("motor-01").build()
        async with KelvinAppTest(app, manifest=manifest) as harness:
            event = Insight(
                resource=KRNAsset("motor-01"),
                tag_name="temperature_high",
                start_date=harness.clock.now(),
                acceptance=acceptance,
            )
            wire = event.to_message()
            wire.payload.acceptance = acceptance
            await harness.publish(wire)
            await harness.run_until_idle()
        output = capfd.readouterr().out
        assert "motor_insight_received" in output
        assert "motor-01" in output and "temperature_high" in output
        assert events(output, "motor_insight_received")[0]["acceptance"] == acceptance
        assert harness.outputs == []
