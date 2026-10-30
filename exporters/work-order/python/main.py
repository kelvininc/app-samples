"""Forward Work Order requests to a customer-owned SAP adapter."""

import os

import httpx
from kelvin.application import KelvinApp
from kelvin.message import CustomAction

app = KelvinApp()


@app.on_custom_action
async def create_work_order(action: CustomAction) -> None:
    """Validate a request, await the external order ID, and return a correlated result."""
    if action.type != "create_work_order":
        return
    summary = action.payload.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 200:
        await app.publish(
            action.result(
                success=False, message="summary must contain 1 to 200 characters"
            )
        )
        return
    equipment_id = action.payload.get("equipment_id")
    if (
        not isinstance(equipment_id, str)
        or not equipment_id.strip()
        or len(equipment_id) > 100
    ):
        await app.publish(
            action.result(
                success=False, message="equipment_id must contain 1 to 100 characters"
            )
        )
        return
    endpoint = app.app_configuration.get("adapter_url", "")
    token = os.environ.get("WORK_ORDER_TOKEN", "")
    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith("https://")
        or not token
    ):
        await app.publish(
            action.result(
                success=False,
                message="Configure HTTPS adapter_url and WORK_ORDER_TOKEN",
            )
        )
        return
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": str(action.custom_action_id),
                },
                json={"equipment_id": equipment_id.strip(), "summary": summary.strip()},
            )
            response.raise_for_status()
            body = response.json()
            order_id = body.get("work_order_id") if isinstance(body, dict) else None
            if not isinstance(order_id, str) or not order_id.strip():
                raise ValueError("Missing work_order_id")
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        await app.publish(
            action.result(
                success=False,
                message="Order creation unconfirmed; reconcile with the adapter by action ID",
            )
        )
        return
    await app.publish(action.result(success=True, response={"work_order_id": order_id}))


if __name__ == "__main__":
    app.run()
