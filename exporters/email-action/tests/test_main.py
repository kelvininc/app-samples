"""App-flow tests: an 'Email Message' custom action -> integration -> result ack, via the harness."""
from datetime import timedelta
from typing import Optional

import pytest
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction
from kelvin.testing import KelvinAppTest, ManifestBuilder
from pydantic import ValidationError

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
    """`to` accepts a single address or a list of them; both surface as a recipient list."""
    base = {"subject": "s", "message": {"text": "b"}}
    assert EmailPayload(to="a@x.com", **base).recipients == ["a@x.com"]
    assert EmailPayload(to=["a@x.com", " b@x.com "], **base).recipients == ["a@x.com", "b@x.com"]
    assert EmailPayload(to=[], **base).recipients == []


@pytest.mark.parametrize("to", [[None], [123], ["a@x.com", None]])
async def test_payload_rejects_non_string_recipients(to: object) -> None:
    """`to` items must be strings: a null or number fails validation rather than being dropped.

    The last case matters most: a list with one good address and one null used to send to the
    good one and silently discard the rest, so a missed recipient looked like a success.
    """
    with pytest.raises(ValidationError):
        EmailPayload(to=to, subject="s", message={"text": "b"})


@pytest.mark.parametrize("to", [
    "a@x.com, b@x.com",            # comma-separated: only one address or a list is supported
    "a@x.com;b@x.com",             # semicolon-separated
    "opsexample.com",              # no @-sign
    "ops@",                        # nothing after the @
    ["a@x.com", "bad"],            # one good address, one malformed
    "root@mailrelay",              # bare hostname: not a globally deliverable domain
    "ops@192.168.1.10",            # IP literal
    "",                            # an empty string is not an address
    " ,; ",                        # separators only
    ["  "],                        # blank list entry
])
async def test_payload_rejects_invalid_addresses(to: object) -> None:
    """`to` is one address or a list of them, each validated, so anything else fails the payload.

    Multi-address strings are rejected on purpose: "a@x.com, b@x.com" is one malformed address,
    not two. Bare hostnames and IP literals are rejected too, which an internal relay would
    otherwise accept; only globally deliverable domains are allowed.
    """
    with pytest.raises(ValidationError):
        EmailPayload(to=to, subject="s", message={"text": "b"})


async def test_payload_normalizes_addresses() -> None:
    """EmailStr lowercases the domain, trims padding, and unwraps a display-name address."""
    base = {"subject": "s", "message": {"text": "b"}}
    assert EmailPayload(to=" OPS@Example.COM ", **base).recipients == ["OPS@example.com"]
    assert EmailPayload(to="Ops Team <ops@example.com>", **base).recipients == ["ops@example.com"]


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
    ({"to": [None], "subject": "s", "message": {"text": "b"}}, "to.0"),             # non-string recipient
    ({"to": "opsexample.com", "subject": "s", "message": {"text": "b"}}, "valid email"),  # malformed address
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


@pytest.mark.parametrize("to", [[]])
async def test_no_recipients_acks_failure(monkeypatch: pytest.MonkeyPatch, to: object) -> None:
    """An empty list is the one `to` that validates but names nobody; the handler catches it.

    Every other empty form ("" , "  ", " ,; ") is now a payload error, since none of them is an
    address. See test_payload_rejects_invalid_addresses.
    """
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


@pytest.mark.parametrize("payload", [
    {"to": "a@x.com", "subject": "   ", "message": {"text": "b"}},
    {"to": "a@x.com", "subject": "s", "message": {"text": "   "}},
])
async def test_whitespace_only_fields_rejected(payload: dict) -> None:
    """Whitespace is stripped before min_length, so a blank subject or text fails validation."""
    with pytest.raises(ValidationError):
        EmailPayload.model_validate(payload)
