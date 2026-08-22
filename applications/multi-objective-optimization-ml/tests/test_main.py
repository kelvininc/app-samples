from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number, RecommendationMsg
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from main import app
from settings import Settings

_ASSET = "paper-machine-1"
_SET_POINT = "paper_machine_speed_set_point"
_BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _manifest(configuration=None):
    b = ManifestBuilder()
    for stream in main.INPUTS:
        if stream in main.CONTROLLABLE_SET_POINTS:
            b.add_input_output_cc(stream)        # set point: read as input, written as control change
        else:
            b.add_input(stream, "number")
    b.add_asset(_ASSET)
    if configuration is not None:
        b.set_configuration(configuration)
    return b.build()


def _rows(n: int):
    """n dense rows: each 1s timestamp carries all 17 streams (n * 17 messages)."""
    batch = []
    for i in range(n):
        ts = _BASE + timedelta(seconds=i)
        for stream in main.INPUTS:
            batch.append(
                Number(resource=KRNAssetDataStream(_ASSET, stream), payload=float(i), timestamp=ts)
            )
    return batch


class TestOptimize:
    @pytest.mark.asyncio
    async def test_default_window_feeds_model_and_publishes(self, monkeypatch) -> None:
        seen = {}

        def fake_run_model(df):
            seen["columns"] = list(df.columns)
            seen["rows"] = len(df)
            return {_SET_POINT: 42.0, "paper_substance_weight": 99.0}  # measured target must be dropped

        monkeypatch.setattr(main, "run_model", fake_run_model)
        harness = KelvinAppTest(app, manifest=_manifest())
        async with harness:
            await harness.publish_batch(_rows(100))          # default window is 100 rows
            await harness.run_until_idle()

        assert seen["columns"] == main.INPUTS                 # deterministic, model-expected order
        assert seen["rows"] == 100
        recs = [o for o in harness.outputs if isinstance(o, RecommendationMsg)]
        assert len(recs) >= 1
        ccs = recs[0].payload.actions.control_changes
        assert [cc.resource.data_stream for cc in ccs] == [_SET_POINT]  # measurement filtered out
        assert ccs[0].payload == 42.0

    @pytest.mark.asyncio
    async def test_below_window_publishes_nothing(self, monkeypatch) -> None:
        called = False

        def fake_run_model(df):
            nonlocal called
            called = True
            return {_SET_POINT: 42.0}

        monkeypatch.setattr(main, "run_model", fake_run_model)
        harness = KelvinAppTest(app, manifest=_manifest())
        async with harness:
            await harness.publish_batch(_rows(50))            # fewer than the window
            await harness.run_until_idle()

        assert called is False
        assert [o for o in harness.outputs if isinstance(o, RecommendationMsg)] == []

    @pytest.mark.asyncio
    async def test_configured_window_size_changes_the_trigger(self, monkeypatch) -> None:
        monkeypatch.setattr(main, "run_model", lambda df: {_SET_POINT: 7.0})
        harness = KelvinAppTest(app, manifest=_manifest({"window": {"rows": 120}}))
        async with harness:
            await harness.publish_batch(_rows(100))           # below the configured 120: no emit
            await harness.run_until_idle()

        assert [o for o in harness.outputs if isinstance(o, RecommendationMsg)] == []

    @pytest.mark.asyncio
    async def test_model_error_does_not_stop_the_stream(self, monkeypatch) -> None:
        def boom(df):
            raise RuntimeError("model failed")

        monkeypatch.setattr(main, "run_model", boom)
        harness = KelvinAppTest(app, manifest=_manifest())
        async with harness:
            await harness.publish_batch(_rows(100))
            await harness.run_until_idle()

        assert [o for o in harness.outputs if isinstance(o, RecommendationMsg)] == []


class TestSettings:
    def test_rows_below_100_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(window={"rows": 50})

    def test_defaults(self) -> None:
        w = Settings().window
        assert (w.rows, w.retrain_every_rows, w.round_seconds) == (100, 1, 1.0)
