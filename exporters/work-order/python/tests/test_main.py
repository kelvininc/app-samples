"""Verify Exporter dispatch and the external Work Order HTTP boundary."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
import main
import pytest
from kelvin.krn import KRNWorkload
from kelvin.message import CustomAction, CustomActionResultMsg
from kelvin.testing import KelvinAppTest, ManifestBuilder

REQUEST_ID = UUID("44b6c8e2-08ed-4d91-b47a-2c10892a4ef7")
MANIFEST = Path(__file__).resolve().parents[1] / "app.yaml"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summary,status,body,success",
    [
        ("Inspect Motor 01 bearings", 201, {"work_order_id": "WO-1042"}, True),
        ("", 201, {"work_order_id": "WO-1042"}, False),
        ("   ", 201, {"work_order_id": "WO-1042"}, False),
        (123, 201, {"work_order_id": "WO-1042"}, False),
        ("x" * 200, 201, {"work_order_id": "WO-1042"}, True),
        ("x" * 201, 201, {"work_order_id": "WO-1042"}, False),
        ("Inspect Motor 01 bearings", 503, {}, False),
        ("Inspect Motor 01 bearings", 201, {}, False),
        ("Inspect Motor 01 bearings", 201, {"work_order_id": ""}, False),
        ("Inspect Motor 01 bearings", 202, {"status": "queued"}, False),
    ],
)
async def test_work_order_result(
    monkeypatch: pytest.MonkeyPatch,
    summary: object,
    status: int,
    body: dict[str, object],
    success: bool,
) -> None:
    """Only a validated request and a confirmed external ID produce correlated success."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Record an outbound request and return the supplied adapter response."""
        requests.append(request)
        return httpx.Response(status, json=body)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setenv("WORK_ORDER_TOKEN", "test-adapter-token")
    manifest = ManifestBuilder.from_app_yaml(MANIFEST).build()
    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        await harness.publish(
            CustomAction(
                resource=KRNWorkload("edge-node", "work-order-requester"),
                type="create_work_order",
                title="Create a maintenance Work Order",
                payload={"equipment_id": "MOTOR-0001", "summary": summary},
                expiration_date=timedelta(seconds=60),
                custom_action_id=REQUEST_ID,
            )
        )
        await harness.run_until_idle()
    results = [
        message
        for message in harness.outputs
        if isinstance(message, CustomActionResultMsg)
    ]
    assert len(results) == 1
    assert results[0].payload.id == REQUEST_ID
    assert results[0].payload.success is success
    valid_summary = (
        isinstance(summary, str) and bool(summary.strip()) and len(summary) <= 200
    )
    assert len(requests) == int(valid_summary)
    if valid_summary:
        assert str(requests[0].url) == "https://maintenance.example.com/work-orders"
        assert requests[0].headers["Idempotency-Key"] == str(REQUEST_ID)
        assert requests[0].headers["Authorization"] == "Bearer test-adapter-token"
        assert json.loads(requests[0].content) == {
            "equipment_id": "MOTOR-0001",
            "summary": summary,
        }
    if success:
        assert results[0].payload.response == {"work_order_id": "WO-1042"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "token",
        "endpoint_type",
        "equipment_missing",
        "equipment_type",
        "equipment_blank",
        "equipment_long",
        "timeout",
        "invalid_json",
    ],
)
async def test_unconfirmed_order(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """Configuration and transport failures never report a confirmed Work Order."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Simulate an uncertain external outcome without contacting SAP."""
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Adapter did not respond", request=request)
        return httpx.Response(201, content=b"invalid-json")

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setenv("WORK_ORDER_TOKEN", "test-adapter-token")
    if failure == "token":
        monkeypatch.delenv("WORK_ORDER_TOKEN")
    builder = ManifestBuilder.from_app_yaml(MANIFEST)
    if failure in {"endpoint_type"}:
        builder.set_configuration(
            {
                "adapter_url": 42
                if failure == "endpoint_type"
                else "https://maintenance.example.com/work-orders",
            }
        )
    payload: dict[str, object] = {
        "equipment_id": "MOTOR-0001",
        "summary": "Inspect Motor 01 bearings",
    }
    if failure == "equipment_missing":
        payload.pop("equipment_id")
    elif failure == "equipment_type":
        payload["equipment_id"] = 42
    elif failure == "equipment_blank":
        payload["equipment_id"] = "   "
    elif failure == "equipment_long":
        payload["equipment_id"] = "x" * 101
    async with KelvinAppTest(main.app, manifest=builder.build()) as harness:
        await harness.publish(
            CustomAction(
                resource=KRNWorkload("edge-node", "work-order-requester"),
                type="create_work_order",
                title="Create a maintenance Work Order",
                payload=payload,
                expiration_date=timedelta(seconds=60),
                custom_action_id=REQUEST_ID,
            )
        )
        await harness.run_until_idle()
    result = next(
        message
        for message in harness.outputs
        if isinstance(message, CustomActionResultMsg)
    )
    assert result.payload.id == REQUEST_ID
    assert result.payload.success is False
    assert len(requests) == (
        0
        if failure
        in {
            "token",
            "endpoint_type",
            "equipment_missing",
            "equipment_type",
            "equipment_blank",
            "equipment_long",
        }
        else 1
    )
