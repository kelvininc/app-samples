"""Test the Event Detection telemetry and Recommendation contract."""

from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number, RecommendationMsg, String
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app

MANIFEST = Path(__file__).resolve().parents[1] / "app.yaml"
DEFAULTS = yaml.safe_load(MANIFEST.read_text())["defaults"]["parameters"]


class TestMotorTemperature:
    """Drive the registered handler with source readings and Asset settings."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("temperature", [-40, 0, 80, 81])
    @pytest.mark.parametrize("closed_loop", [False, True])
    async def test_telemetry_and_threshold(
        self, temperature: float, closed_loop: bool
    ) -> None:
        """Convert every reading and recommend only above the exact threshold."""
        manifest = (
            ManifestBuilder.from_app_yaml(MANIFEST)
            .add_asset(
                "motor-01", parameters={**DEFAULTS, "kelvin_closed_loop": closed_loop}
            )
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            timestamp = harness.clock.now()
            expiry = timestamp + timedelta(minutes=5)
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("motor-01", "temperature_c"),
                    payload=temperature,
                    timestamp=timestamp,
                )
            )
            await harness.run_until_idle()
            telemetry = [m for m in harness.outputs if isinstance(m, Number)]
            recommendations = [
                m for m in harness.outputs if isinstance(m, RecommendationMsg)
            ]
            assert len(telemetry) == 1
            assert telemetry[0].resource == KRNAssetDataStream(
                "motor-01", "temperature_f"
            )
            assert telemetry[0].timestamp == timestamp
            assert telemetry[0].payload == pytest.approx(temperature * 9 / 5 + 32)
            assert len(recommendations) == int(temperature > 80)
            if recommendations:
                result = recommendations[0]
                assert result.resource.asset == "motor-01"
                assert (result.payload.state == "auto_accepted") == closed_loop
                assert result.payload.expiration_date == expiry
                assert result.payload.evidences
                change = result.payload.actions.control_changes[0]
                assert change.resource == KRNAssetDataStream(
                    "motor-01", "motor_speed_setpoint"
                )
                assert change.payload == 1000
                assert change.expiration_date == expiry
                assert change.retries == 0

    @pytest.mark.asyncio
    async def test_each_asset_uses_its_own_parameters(self) -> None:
        """Keep thresholds, setpoints, and telemetry mapped to their source Asset."""
        manifest = (
            ManifestBuilder.from_app_yaml(MANIFEST)
            .add_asset(
                "motor-01", parameters={**DEFAULTS, "temperature_max_threshold": 90}
            )
            .add_asset(
                "motor-02",
                parameters={
                    **DEFAULTS,
                    "temperature_max_threshold": 60,
                    "speed_decrease_set_point": 1500,
                },
            )
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            for asset in ["motor-01", "motor-02"]:
                await harness.publish(
                    Number(
                        resource=KRNAssetDataStream(asset, "temperature_c"), payload=81
                    )
                )
            await harness.run_until_idle()
            assert {
                m.resource.asset for m in harness.outputs if isinstance(m, Number)
            } == {"motor-01", "motor-02"}
            recommendations = [
                m for m in harness.outputs if isinstance(m, RecommendationMsg)
            ]
            assert len(recommendations) == 1
            assert recommendations[0].resource.asset == "motor-02"
            assert recommendations[0].payload.actions.control_changes[0].payload == 1500

    @pytest.mark.asyncio
    async def test_unrelated_input_is_ignored(self) -> None:
        """Other declared input resources must not trigger this handler."""
        manifest = (
            ManifestBuilder.from_app_yaml(MANIFEST)
            .add_asset("motor-01", parameters=DEFAULTS)
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                String(
                    resource=KRNAssetDataStream("motor-01", "motor_state"),
                    payload="running",
                )
            )
            await harness.run_until_idle()
            assert harness.outputs == []
