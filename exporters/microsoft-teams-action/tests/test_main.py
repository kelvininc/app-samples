"""App-flow tests: a 'Teams Message' custom action -> integration -> result ack, via the harness."""
from datetime import timedelta

import pytest
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from teams_integration import TeamsSendError

pytestmark = pytest.mark.asyncio

URL = "https://example.webhook.office.com/webhookb2/abc/IncomingWebhook/def"


class FakeIntegration:
    """Stands in for TeamsIntegration; records the call and succeeds or raises."""

    calls: list[tuple] = []
    raise_exc: Exception | None = None

    def __init__(self, cfg: object) -> None:
        self.cfg = cfg

    async def send_message(self, text: str, title=None) -> None:
        FakeIntegration.calls.append((text, title))
        if FakeIntegration.raise_exc:
            raise FakeIntegration.raise_exc

    async def close(self) -> None:
        pass


def _reset() -> None:
    main._integration = None
    FakeIntegration.calls = []
    FakeIntegration.raise_exc = None


def _manifest(webhook_url: str | None = URL):
    builder = ManifestBuilder.from_app_yaml().add_custom_action_input("Teams Message")
    return builder.set_configuration({"teams": {"webhook_url": webhook_url}} if webhook_url else {"teams": {}}).build()


def _action(payload: dict) -> CustomAction:
    return CustomAction(
        resource=KRNAsset("test-asset-1"),
        type="Teams Message",
        title="Teams Test",
        expiration_date=timedelta(days=1),
        payload=payload,
    )


async def test_handler_registered() -> None:
    """The custom-action handler is wired on the app."""
    assert main.app.on_custom_action is not None


async def test_action_posts_and_acks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid Teams Message action calls the integration and publishes a success result."""
    _reset()
    monkeypatch.setattr(main, "TeamsIntegration", FakeIntegration)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"title": "Pump 3", "message": {"text": "temp high"}}))
        await harness.run_until_idle(timeout=5.0)

        assert FakeIntegration.calls == [("temp high", "Pump 3")]
        assert len(harness.outputs) == 1
        result = harness.outputs[0].payload
        assert result.success is True and result.message == "Teams message sent"


async def test_malformed_message_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A payload with `message` as a plain string (not the documented object) acks failure."""
    _reset()
    monkeypatch.setattr(main, "TeamsIntegration", FakeIntegration)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"message": "a string"}))
        await harness.run_until_idle(timeout=5.0)

        assert FakeIntegration.calls == []
        assert len(harness.outputs) == 1
        result = harness.outputs[0].payload
        assert result.success is False and result.message.startswith("Invalid payload: message:")


async def test_missing_text_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An action without message text acks failure and never calls the integration."""
    _reset()
    monkeypatch.setattr(main, "TeamsIntegration", FakeIntegration)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"title": "no body"}))
        await harness.run_until_idle(timeout=5.0)

        assert FakeIntegration.calls == []
        assert len(harness.outputs) == 1
        result = harness.outputs[0].payload
        assert result.success is False and result.message.startswith("Invalid payload:")


async def test_send_failure_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TeamsSendError from the integration becomes a failure ack carrying its message."""
    _reset()
    monkeypatch.setattr(main, "TeamsIntegration", FakeIntegration)
    FakeIntegration.raise_exc = TeamsSendError("Teams webhook returned 500")

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"message": {"text": "temp high"}}))
        await harness.run_until_idle(timeout=5.0)

        assert len(harness.outputs) == 1
        result = harness.outputs[0].payload
        assert result.success is False and result.message == "Teams webhook returned 500"


async def test_action_before_startup_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An action dispatched before on_connect builds the integration acks failure instead of crashing."""
    _reset()
    monkeypatch.setattr(main, "TeamsIntegration", FakeIntegration)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        # Simulate the startup race: the SDK's read loop can dispatch an action buffered
        # alongside the manifest before on_connect has built the integration.
        main._integration = None
        await harness.publish(_action({"message": {"text": "temp high"}}))
        await harness.run_until_idle(timeout=5.0)

        assert FakeIntegration.calls == []
        assert len(harness.outputs) == 1
        result = harness.outputs[0].payload
        assert result.success is False and result.message == "Connector is still starting; retry shortly"


async def test_unexpected_action_type_is_ignored() -> None:
    """A non-'Teams Message' action is logged and dropped without acking (handler-level check)."""
    _reset()

    class _Other:
        type = "Reboot Device"

    await main.on_custom_action(_Other())   # returns early; no integration/app.publish needed


async def test_invalid_configuration_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing webhook URL terminates the app (SystemExit) instead of starting half-configured."""
    _reset()
    monkeypatch.setattr(main, "TeamsIntegration", FakeIntegration)

    harness = KelvinAppTest(main.app, manifest=_manifest(webhook_url=None))
    try:
        with pytest.raises(SystemExit) as excinfo:
            await harness.connect()
        assert excinfo.value.code == 1
        assert main._integration is None
    finally:
        # SystemExit bypasses the harness's `except Exception` cleanup; disconnect manually
        # so the read loop doesn't leak into other tests.
        await main.app.disconnect()
