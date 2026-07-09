"""App-flow tests: an 'Email Message' custom action -> integration -> result ack, via the harness."""
from datetime import timedelta
from typing import Optional

import pytest
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from email_integration import EmailSendError
from main import EmailPayload

pytestmark = pytest.mark.asyncio


class FakeIntegration:
    calls: list[tuple] = []
    error: Optional[EmailSendError] = None

    def __init__(self, cfg: object) -> None:
        self.cfg = cfg

    async def send(self, to: list[str], subject: str, body: str) -> None:
        FakeIntegration.calls.append((to, subject, body))
        if FakeIntegration.error is not None:
            raise FakeIntegration.error


def _reset() -> None:
    main._integration = None
    FakeIntegration.calls = []
    FakeIntegration.error = None


def _manifest(host: str = "smtp.example.com"):
    return (
        ManifestBuilder.from_app_yaml()
        .add_custom_action_input("Email Message")
        .set_configuration({"smtp": {"host": host, "from_address": "alerts@example.com",
                                     "auth": {"method": "none"}}})
        .build()
    )


def _action(payload: dict) -> CustomAction:
    return CustomAction(
        resource=KRNAsset("test-asset-1"),
        type="Email Message",
        title="Email Test",
        expiration_date=timedelta(days=1),
        payload=payload,
    )


async def test_handler_registered() -> None:
    assert main.app.on_custom_action is not None


async def test_payload_recipients_normalization() -> None:
    """`to` accepts a list or a comma/semicolon-separated string; blanks are dropped."""
    base = {"subject": "s", "message": {"text": "b"}}
    assert EmailPayload(to=["a@x.com", " b@x.com "], **base).recipients == ["a@x.com", "b@x.com"]
    assert EmailPayload(to="a@x.com, b@x.com; c@x.com", **base).recipients == ["a@x.com", "b@x.com", "c@x.com"]
    assert EmailPayload(to="", **base).recipients == []
    assert EmailPayload(to=[None, "  "], **base).recipients == []


async def test_action_sends_and_acks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    monkeypatch.setattr(main, "EmailIntegration", FakeIntegration)
    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"to": "ops@x.com", "subject": "Pump 3", "message": {"text": "temp high"}}))
        await harness.run_until_idle(timeout=5.0)
        assert FakeIntegration.calls == [(["ops@x.com"], "Pump 3", "temp high")]
        assert len(harness.outputs) == 1 and harness.outputs[0].payload.success is True
        assert harness.outputs[0].payload.message == "Email sent"


@pytest.mark.parametrize("payload,reason", [
    ({"message": "a string"}, "Invalid payload"),                        # message not an object; no to/subject
    ({"subject": "s", "message": {"text": "b"}}, "to: Field required"),  # no 'to' at all
    ({"to": "a@x.com", "message": {"text": "b"}}, "subject"),            # no subject
    ({"to": "a@x.com", "subject": "s"}, "message"),                      # no body
    ({"to": "a@x.com", "subject": "s", "message": {"text": ""}}, "message.text"),  # empty body
])
async def test_malformed_payload_acks_failure(monkeypatch: pytest.MonkeyPatch, payload: dict, reason: str) -> None:
    _reset()
    monkeypatch.setattr(main, "EmailIntegration", FakeIntegration)
    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action(payload))
        await harness.run_until_idle(timeout=5.0)
        assert FakeIntegration.calls == []
        assert len(harness.outputs) == 1 and harness.outputs[0].payload.success is False
        message = harness.outputs[0].payload.message or ""
        assert message.startswith("Invalid payload: ") and reason in message


@pytest.mark.parametrize("to", ["", " ,; ", []])
async def test_no_recipients_acks_failure(monkeypatch: pytest.MonkeyPatch, to: object) -> None:
    """A present-but-empty `to` passes payload validation but fails the recipients check."""
    _reset()
    monkeypatch.setattr(main, "EmailIntegration", FakeIntegration)
    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"to": to, "subject": "s", "message": {"text": "b"}}))
        await harness.run_until_idle(timeout=5.0)
        assert FakeIntegration.calls == []
        assert len(harness.outputs) == 1 and harness.outputs[0].payload.success is False
        assert harness.outputs[0].payload.message == "No recipients ('to') specified"


async def test_send_failure_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An EmailSendError from the integration becomes a failure ack with its message verbatim."""
    _reset()
    monkeypatch.setattr(main, "EmailIntegration", FakeIntegration)
    FakeIntegration.error = EmailSendError("Failed to send email (relay refused)")
    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        await harness.publish(_action({"to": "ops@x.com", "subject": "s", "message": {"text": "b"}}))
        await harness.run_until_idle(timeout=5.0)
        assert len(FakeIntegration.calls) == 1
        assert len(harness.outputs) == 1 and harness.outputs[0].payload.success is False
        assert harness.outputs[0].payload.message == "Failed to send email (relay refused)"


async def test_action_before_integration_built_acks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An action dispatched before on_connect builds the integration acks failure, not a crash."""
    _reset()
    monkeypatch.setattr(main, "EmailIntegration", FakeIntegration)
    async with KelvinAppTest(main.app, manifest=_manifest()) as harness:
        main._integration = None   # simulate the SDK dispatching a buffered action before on_connect finishes
        await harness.publish(_action({"to": "ops@x.com", "subject": "s", "message": {"text": "b"}}))
        await harness.run_until_idle(timeout=5.0)
        assert FakeIntegration.calls == []
        assert len(harness.outputs) == 1 and harness.outputs[0].payload.success is False
        assert harness.outputs[0].payload.message == "Connector is still starting; retry shortly"


async def test_unexpected_action_type_is_ignored() -> None:
    _reset()

    class _Other:
        type = "Reboot Device"

    await main.on_custom_action(_Other())   # returns early; no _integration/app.publish needed


async def test_invalid_configuration_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad config raises SystemExit out of on_connect: in production this kills the process."""
    _reset()
    monkeypatch.setattr(main, "EmailIntegration", FakeIntegration)
    harness = KelvinAppTest(main.app, manifest=_manifest(host=""))
    try:
        with pytest.raises(SystemExit) as exc_info:
            await harness.connect()
        assert exc_info.value.code == 1
        assert main._integration is None
    finally:
        # SystemExit bypasses the harness's `except Exception` cleanup; disconnect manually
        # so the read loop doesn't leak into other tests.
        await main.app.disconnect()
