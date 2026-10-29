from datetime import timedelta
from uuid import UUID

import pytest
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message import (
    CustomActionMsg,
    CustomActionReport,
    CustomActionState,
    Number,
)
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


class TestDiagnosticPublisher:
    """Check threshold publication and every terminal-report callback."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("temperature,expected", [(79, 0), (80, 0), (81, 1)])
    async def test_threshold(self, temperature: float, expected: int) -> None:
        """Publish only above the threshold and preserve the source Asset."""
        manifest = (
            ManifestBuilder()
            .add_input("temperature_c", "number")
            .add_custom_action_output("run_diagnostics")
            .add_asset("motor-01")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("motor-01", "temperature_c"),
                    payload=temperature,
                )
            )
            await harness.run_until_idle()
        actions = [m for m in harness.outputs if isinstance(m, CustomActionMsg)]
        assert len(actions) == expected
        if actions:
            assert actions[0].resource == KRNAsset("motor-01")
            assert actions[0].type.type == "run_diagnostics"
            assert actions[0].payload.payload == {"include_history": True}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "state",
        [
            CustomActionState.COMPLETED,
            CustomActionState.FAILED,
            CustomActionState.CANCELLED,
        ],
    )
    async def test_report(
        self, state: CustomActionState, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Dispatch every terminal report through the registered callback."""
        manifest = (
            ManifestBuilder()
            .add_custom_action_output("run_diagnostics")
            .add_asset("motor-01")
            .build()
        )
        action_id = UUID("44b6c8e2-08ed-4d91-b47a-2c10892a4ef7")
        async with KelvinAppTest(app, manifest=manifest) as harness:
            now = harness.clock.now()
            await harness.publish(
                CustomActionReport(
                    resource=KRNAsset("motor-01"),
                    action_id=action_id,
                    type="run_diagnostics",
                    title="Inspect motor",
                    state=state,
                    expiration_date=now + timedelta(seconds=30),
                    created=now,
                    updated=now,
                    created_by="krn:wl:temperature-monitor",
                    updated_by="krn:wl:diagnostic-receiver",
                    response={"history_requested": True}
                    if state == CustomActionState.COMPLETED
                    else None,
                )
            )
            await harness.run_until_idle()
        output = capfd.readouterr().out
        assert "diagnostics_report" in output
        assert str(action_id) in output
        assert state.value in output
