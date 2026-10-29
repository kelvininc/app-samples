import json

import pytest
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


def events(output: str, name: str) -> list[dict]:
    """Read structured events without depending on JSON field order."""
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [record for record in records if record.get("event") == name]


class TestPropertyReader:
    """Read assigned metadata with an explicit policy for missing values."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "properties,expected", [({"manufacturer": "ABB"}, "ABB"), ({}, "unknown")]
    )
    async def test_optional_property(
        self,
        properties: dict[str, str],
        expected: str,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """Use the Asset's property or the documented fallback."""
        manifest = (
            ManifestBuilder.from_app_yaml()
            .add_asset(
                "motor-01", parameters={"temperature_limit": 80}, properties=properties
            )
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.run_until_idle()
        output = capfd.readouterr().out
        assert (
            "motor_manufacturer" in output
            and "motor-01" in output
            and expected in output
        )
