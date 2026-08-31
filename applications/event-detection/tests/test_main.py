import pytest

from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number, RecommendationMsg
from kelvin.testing import KelvinAppTest, ManifestBuilder

from main import app

_ASSET = "motor-1"
_THRESHOLD = 59
_SET_POINT = 1000


def _manifest(*, closed_loop: bool):
    """A single motor asset wired for the temperature input and the speed control change."""
    return (
        ManifestBuilder()
        .add_input("motor_temperature", "number")
        .add_control_change_output("motor_speed_set_point", "number")
        .add_asset(
            _ASSET,
            parameters={
                "temperature_max_threshold": _THRESHOLD,
                "speed_decrease_set_point": _SET_POINT,
                "kelvin_closed_loop": closed_loop,
            },
        )
        .build()
    )


async def _publish_temperature(harness, value: float) -> None:
    await harness.publish(
        Number(resource=KRNAssetDataStream(_ASSET, "motor_temperature"), payload=value)
    )
    await harness.run_until_idle()


class TestOnMotorTemperature:
    @pytest.mark.asyncio
    async def test_at_or_below_threshold_publishes_nothing(self) -> None:
        harness = KelvinAppTest(app, manifest=_manifest(closed_loop=False))
        async with harness:
            await _publish_temperature(harness, float(_THRESHOLD))  # exactly at threshold: no action
        assert [o for o in harness.outputs if isinstance(o, RecommendationMsg)] == []

    @pytest.mark.asyncio
    async def test_over_threshold_recommends_speed_decrease(self) -> None:
        harness = KelvinAppTest(app, manifest=_manifest(closed_loop=False))
        async with harness:
            await _publish_temperature(harness, 75.0)

        recs = [o for o in harness.outputs if isinstance(o, RecommendationMsg)]
        assert len(recs) == 1
        payload = recs[0].payload
        assert payload.type == "decrease_speed"
        assert payload.state != "auto_accepted"                    # operator must approve
        assert len(payload.actions.control_changes) == 1
        assert payload.actions.control_changes[0].payload == _SET_POINT

    @pytest.mark.asyncio
    async def test_closed_loop_auto_accepts_the_recommendation(self) -> None:
        harness = KelvinAppTest(app, manifest=_manifest(closed_loop=True))
        async with harness:
            await _publish_temperature(harness, 75.0)

        recs = [o for o in harness.outputs if isinstance(o, RecommendationMsg)]
        assert len(recs) == 1
        assert recs[0].payload.state == "auto_accepted"
