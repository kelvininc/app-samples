from datetime import timedelta
from uuid import UUID

import pytest
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction, CustomActionResultMsg
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app


class TestDiagnosticRequests:
    """Check success, malformed payloads, ignored types and correlation."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "flag,success", [(True, True), (False, True), ("yes", False)]
    )
    async def test_result(self, flag: object, success: bool) -> None:
        """Return the request ID and validate the diagnostic flag."""
        manifest = (
            ManifestBuilder()
            .add_custom_action_input("run_diagnostics")
            .add_asset("motor-01")
            .build()
        )
        request_id = UUID("44b6c8e2-08ed-4d91-b47a-2c10892a4ef7")
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                CustomAction(
                    resource=KRNAsset("motor-01"),
                    type="run_diagnostics",
                    title="Inspect Motor 01",
                    payload={"include_history": flag},
                    expiration_date=timedelta(seconds=30),
                    custom_action_id=request_id,
                )
            )
            await harness.run_until_idle()
        results = [m for m in harness.outputs if isinstance(m, CustomActionResultMsg)]
        assert len(results) == 1
        assert results[0].payload.id == request_id
        assert results[0].payload.success is success
        if success:
            assert results[0].payload.response == {"history_requested": flag}
        else:
            assert results[0].payload.message == "include_history must be a Boolean"

    @pytest.mark.asyncio
    async def test_missing_flag(self) -> None:
        """Default an omitted flag to false and preserve the request correlation."""
        manifest = (
            ManifestBuilder()
            .add_custom_action_input("run_diagnostics")
            .add_asset("motor-01")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            await harness.publish(
                CustomAction(
                    resource=KRNAsset("motor-01"),
                    type="run_diagnostics",
                    title="Inspect Motor 01",
                    payload={},
                    expiration_date=timedelta(seconds=30),
                )
            )
            await harness.run_until_idle()
        results = [m for m in harness.outputs if isinstance(m, CustomActionResultMsg)]
        assert len(results) == 1
        assert results[0].payload.success is True
        assert results[0].payload.response == {"history_requested": False}
        assert results[0].payload.id == harness.inputs[0].id
