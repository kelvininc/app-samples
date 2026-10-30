"""Tests for filtered consumption and SDK-managed task cleanup."""

from pathlib import Path

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Boolean, Number, String
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app, motor_overheated


class TestPredicate:
    """Select the intended resource, type, and strict threshold."""

    @pytest.mark.parametrize(
        "message,expected",
        [
            (
                Number(
                    resource=KRNAssetDataStream("motor-01", "temperature_c"), payload=81
                ),
                True,
            ),
            (
                Number(
                    resource=KRNAssetDataStream("motor-01", "temperature_c"), payload=80
                ),
                False,
            ),
            (
                Number(
                    resource=KRNAssetDataStream("motor-02", "temperature_c"), payload=90
                ),
                False,
            ),
            (
                Number(
                    resource=KRNAssetDataStream("motor-01", "motor_speed"), payload=90
                ),
                False,
            ),
            (Number(payload=90), False),
            (
                String(
                    resource=KRNAssetDataStream("motor-01", "temperature_c"),
                    payload="90",
                ),
                False,
            ),
        ],
    )
    def test_selects_high_temperature(
        self, message: Number | String, expected: bool
    ) -> None:
        """Reject unrelated types and resources as well as the threshold boundary."""
        assert motor_overheated(message) is expected


class TestConsumeHotReadings:
    """Run the background consumer through the Kelvin testing transport."""

    @pytest.mark.asyncio
    async def test_alerts_only_for_selected_readings(self) -> None:
        """Keep the reading timestamp and stop the consumer on context exit."""
        manifest = (
            ManifestBuilder.from_app_yaml(Path("app.yaml"))
            .add_asset("motor-01")
            .add_asset("motor-02")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.run_until_idle()
            timestamp = harness.clock.now()
            for asset, value in [("motor-01", 80), ("motor-02", 90), ("motor-01", 81)]:
                await harness.publish(
                    Number(
                        resource=KRNAssetDataStream(asset, "temperature_c"),
                        payload=value,
                        timestamp=timestamp,
                    )
                )
            await harness.run_until_idle()
            alerts = [
                message for message in harness.outputs if isinstance(message, Boolean)
            ]
            assert len(alerts) == 1
            assert alerts[0].resource == KRNAssetDataStream(
                "motor-01", "temperature_alert"
            )
            assert alerts[0].payload is True
            assert alerts[0].timestamp == timestamp
        assert not app.is_connected
