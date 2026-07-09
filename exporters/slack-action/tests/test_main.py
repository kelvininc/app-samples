"""App-flow tests: a 'Slack Message' custom action -> integration -> result ack, via the harness."""
from datetime import timedelta

import pytest
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from slack_integration import SlackSendError

pytestmark = pytest.mark.asyncio


class FakeIntegration:
    """Stands in for SlackIntegration; records calls and optionally raises a canned error."""

    calls: list[tuple] = []
    error: SlackSendError | None = None

    def __init__(self, cfg: object) -> None:
        self.cfg = cfg

    async def send_message(self, channel_name: str, message: str) -> None:
        FakeIntegration.calls.append((channel_name, message))
        if FakeIntegration.error is not None:
            raise FakeIntegration.error


def _reset() -> None:
    main._integration = None
    FakeIntegration.calls = []
    FakeIntegration.error = None


def _manifest(token: str | None = "xoxb-test"):
    builder = (
        ManifestBuilder.from_app_yaml()
        .add_custom_action_input("Slack Message")
    )
    builder = builder.set_configuration({"slack": {"token": token}} if token else {"slack": {}})
    return builder.build()


def _action(payload: dict) -> CustomAction:
    return CustomAction(
        resource=KRNAsset("test-asset-1"),
        type="Slack Message",
        title="Slack Test",
        expiration_date=timedelta(days=1),
        payload=payload,
    )


async def test_handler_registered() -> None:
    """The custom-action handler is wired on the app."""
    assert main.app.on_custom_action is not None


async def test_action_posts_and_acks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid Slack Message action calls the integration and publishes a success result."""
    _reset()
    monkeypatch.setattr(main, "SlackIntegration", FakeIntegration)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"channel": "alerts", "message": {"text": "hello"}}))
        await harness.run_until_idle(timeout=5.0)

        assert FakeIntegration.calls == [("alerts", "hello")]
        results = harness.outputs
        assert len(results) == 1 and results[0].payload.success is True
        assert results[0].payload.message == "Slack message sent"


@pytest.mark.parametrize(
    "payload",
    [
        {"message": {"text": "hello"}},                     # missing channel
        {"channel": "alerts"},                              # missing message
        {"channel": "alerts", "message": "hello"},          # message is a string, not an object
        {"channel": "", "message": {"text": "hello"}},      # blank channel
        {"channel": "alerts", "message": {"text": ""}},     # blank text
    ],
)
async def test_malformed_payload_acks_failure(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    """A malformed payload acks failure with validation details and never calls the integration."""
    _reset()
    monkeypatch.setattr(main, "SlackIntegration", FakeIntegration)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action(payload))
        await harness.run_until_idle(timeout=5.0)

        assert FakeIntegration.calls == []
        results = harness.outputs
        assert len(results) == 1 and results[0].payload.success is False
        assert (results[0].payload.message or "").startswith("Invalid payload: ")


async def test_send_failure_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SlackSendError from the integration is acked as a failure with its message."""
    _reset()
    monkeypatch.setattr(main, "SlackIntegration", FakeIntegration)
    FakeIntegration.error = SlackSendError("Slack channel 'alerts' not found")

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"channel": "alerts", "message": {"text": "hello"}}))
        await harness.run_until_idle(timeout=5.0)

        results = harness.outputs
        assert len(results) == 1 and results[0].payload.success is False
        assert results[0].payload.message == "Slack channel 'alerts' not found"


async def test_action_before_connect_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An action dispatched before on_connect builds the integration acks failure with a retry hint."""
    _reset()
    monkeypatch.setattr(main, "SlackIntegration", FakeIntegration)

    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        main._integration = None    # simulate the startup race: action arrives before on_connect finishes
        await harness.publish(_action({"channel": "alerts", "message": {"text": "hello"}}))
        await harness.run_until_idle(timeout=5.0)

        assert FakeIntegration.calls == []
        results = harness.outputs
        assert len(results) == 1 and results[0].payload.success is False
        assert results[0].payload.message == "Connector is still starting; retry shortly"


async def test_unexpected_action_type_is_ignored() -> None:
    """A non-'Slack Message' action is logged and dropped without acking (handler-level check)."""
    _reset()

    class _Other:
        type = "Reboot Device"

    await main.on_custom_action(_Other())   # returns early; no integration/app.publish needed


async def test_invalid_configuration_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing token makes on_connect raise SystemExit instead of starting half-configured."""
    _reset()
    monkeypatch.setattr(main, "SlackIntegration", FakeIntegration)
    try:
        with pytest.raises(SystemExit) as exc_info:
            async with KelvinAppTest(main.app, manifest=_manifest(token=None)):
                pass
        assert exc_info.value.code == 1
    finally:
        # SystemExit is a BaseException, so the harness's `except Exception` cleanup in
        # connect() doesn't run; stop the app's read loop so later tests get a clean app.
        await main.app.disconnect()
    assert main._integration is None
