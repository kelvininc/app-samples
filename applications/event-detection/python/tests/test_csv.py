"""Replay a finite CSV and assert the resulting Recommendation."""

from pathlib import Path

import pytest
from kelvin.message import RecommendationMsg
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest
from kelvin.testing.sources import CSVSource
from main import app


@pytest.mark.asyncio
async def test_recorded_temperatures(motor_manifest: RuntimeManifest) -> None:
    """Replay below, at, and above-threshold rows in file order."""
    source = CSVSource(
        Path("data/test-data.csv"),
        ignore_timestamps=True,  # Use the harness clock for this threshold-only test.
    ).with_columns("temperature_c")
    harness = KelvinAppTest(app, manifest=motor_manifest).add_source(source)
    async with harness:
        await harness.run_until_idle()
        readings = [message.payload for message in harness.inputs]
        recommendations = [
            message
            for message in harness.outputs
            if isinstance(message, RecommendationMsg)
        ]
        assert readings == [45.0, 80.0, 81.0]
        assert len(recommendations) == 1
        assert recommendations[0].resource.asset == "motor-01"


@pytest.mark.asyncio
async def test_equipment_column_mapping(
    motor_manifest: RuntimeManifest, tmp_path: Path
) -> None:
    """Map a recorded sensor column and fill a missing Asset name."""
    recorded = tmp_path / "equipment.csv"
    recorded.write_text(
        "recorded_at,equipment,celsius\n"
        "2026-10-14T12:00:00Z,motor-01,81\n"
        "2026-10-14T12:00:01Z,,80\n",
        encoding="utf-8",
    )
    source = (
        CSVSource(
            recorded,
            asset_column="equipment",
            timestamp_column="recorded_at",
            ignore_timestamps=True,
        )
        .with_columns("celsius")
        .with_column_mapping({"celsius": "temperature_c"})
        .with_asset("motor-01")
    )
    async with KelvinAppTest(app, manifest=motor_manifest).add_source(
        source
    ) as harness:
        await harness.run_until_idle()
        assert [message.payload for message in harness.inputs] == [81.0, 80.0]
        assert all(message.resource.asset == "motor-01" for message in harness.inputs)
        recommendations = [
            message
            for message in harness.outputs
            if isinstance(message, RecommendationMsg)
        ]
        assert len(recommendations) == 1
