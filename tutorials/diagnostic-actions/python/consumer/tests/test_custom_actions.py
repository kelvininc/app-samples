"""Test a Custom Action result and its correlation with the injected request."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction, CustomActionResultMsg
from kelvin.message.runtime_manifest import RuntimeManifest
from kelvin.testing import KelvinAppTest
from main import app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "include_history,success", [(True, True), (False, True), ("yes", False)]
)
async def test_diagnostic_request(
    motor_manifest: RuntimeManifest, include_history: Any, success: bool
) -> None:
    """Validate the flag and correlate the result with the original request ID."""
    action_id = UUID("44b6c8e2-08ed-4d91-b47a-2c10892a4ef7")
    async with KelvinAppTest(app, manifest=motor_manifest) as harness:
        await harness.publish(
            CustomAction(
                resource=KRNAsset("motor-01"),
                type="run_diagnostics",
                title="Inspect diagnostic request",
                expiration_date=timedelta(seconds=30),
                payload={"include_history": include_history},
                custom_action_id=action_id,
            )
        )
        await harness.run_until_idle()
        results = [
            message
            for message in harness.outputs
            if isinstance(message, CustomActionResultMsg)
        ]
        assert len(results) == 1
        assert results[0].payload.id == action_id
        assert results[0].payload.success is success
        if success:
            assert results[0].payload.response == {"history_requested": include_history}
        else:
            assert results[0].payload.message == "include_history must be a Boolean"
