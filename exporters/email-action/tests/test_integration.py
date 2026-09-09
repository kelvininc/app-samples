"""Real-server integration tests against a live Mailpit SMTP server (testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. These drive the REAL
`EmailIntegration` (aiosmtplib over the wire) against a Mailpit container, then query Mailpit's HTTP
API to confirm what actually arrived. Most cases go through the full exporter path a real deployment
uses: publish a `CustomAction` into `KelvinAppTest`, let `on_custom_action` send via the real
integration, and assert BOTH the result ack AND the message Mailpit received.

Two Mailpit containers are used:
- `mailpit`: accepts any/no auth (the happy path).
- `mailpit_auth`: requires SMTP AUTH, so an unauthenticated send is rejected with `530`.

Mailpit accumulates messages for the life of its (module-scoped) container, so the primary server is
cleared before each test (`_clear`), letting tests assert exact message counts.

Not covered here (kept as unit tests in test_email_integration.py / test_settings.py): TLS-success and
AUTH-success. Mailpit serves a self-signed cert and the production client hardcodes cert validation, so
`use_tls=True` fails cert verification before AUTH can happen; and settings refuses username_password
without TLS. `test_tls_cert_failure_acks_failure` pins that cert-verification failure surfaces as a
failed ack, asserting only on the ack (not on ssl error text) to stay robust.
"""
import asyncio
import json
import urllib.request
from datetime import timedelta

import pytest
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from email_integration import EmailIntegration
from settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_FROM = "alerts@plant.example"


@pytest.fixture(scope="module")
def mailpit():
    """One Mailpit server (any/no auth) for the module; yields (smtp_host, smtp_port, api_base_url)."""
    from testcontainers.mailpit import MailpitContainer

    with MailpitContainer() as c:
        yield c.get_container_host_ip(), c.get_exposed_smtp_port(), c.get_base_api_url()


@pytest.fixture(scope="module")
def mailpit_auth():
    """A second Mailpit that REQUIRES SMTP AUTH; an unauthenticated send is rejected with 530."""
    from testcontainers.mailpit import MailpitContainer, MailpitUser

    with MailpitContainer(users=[MailpitUser("relay", "s3cret")]) as c:
        yield c.get_container_host_ip(), c.get_exposed_smtp_port(), c.get_base_api_url()


@pytest.fixture(autouse=True)
def _clear_primary(mailpit):
    """Clear the primary Mailpit before each test so message-count assertions are exact."""
    _clear(mailpit[2])
    yield


def _messages(api_base_url: str) -> list[dict]:
    with urllib.request.urlopen(f"{api_base_url}/api/v1/messages", timeout=10) as resp:
        return json.load(resp)["messages"]


def _message(api_base_url: str, msg_id: str) -> dict:
    """Fetch a single message (includes the decoded Text/HTML body)."""
    with urllib.request.urlopen(f"{api_base_url}/api/v1/message/{msg_id}", timeout=10) as resp:
        return json.load(resp)


def _clear(api_base_url: str) -> None:
    req = urllib.request.Request(f"{api_base_url}/api/v1/messages", method="DELETE")
    urllib.request.urlopen(req, timeout=10).close()


def _config(host: str, port: int, *, use_tls: bool = False, auth: dict | None = None) -> dict:
    return {
        "smtp": {
            "host": host,
            "port": port,
            "use_tls": use_tls,
            "from_address": _FROM,
            "auth": auth or {"method": "none"},
        }
    }


def _manifest(host: str, port: int, *, use_tls: bool = False, auth: dict | None = None):
    """Manifest wiring the 'Email Message' action and pointing the exporter at a Mailpit server."""
    return (
        ManifestBuilder.from_app_yaml()
        .add_custom_action_input("Email Message")
        .set_configuration(_config(host, port, use_tls=use_tls, auth=auth))
        .build()
    )


async def _await_ack(harness, timeout: float = 30.0) -> None:
    """Poll in real time until the handler publishes its result ack (or timeout).

    on_custom_action runs inline in the SDK read loop and awaits a REAL network send;
    run_until_idle returns before that network round-trip completes, so we yield real time
    (asyncio.sleep) to let the read loop finish and drain harness.outputs. The ack is published
    only after send() returns, so its presence also means Mailpit has accepted (or rejected) the mail.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and not harness.outputs:
        await asyncio.sleep(0.05)


def _action(to: object, subject: str, body: str) -> CustomAction:
    return CustomAction(
        resource=KRNAsset("test-asset-1"),
        type="Email Message",
        title="Email Test",
        expiration_date=timedelta(days=1),
        payload={"to": to, "subject": subject, "message": {"text": body}},
    )


async def test_email_is_delivered(mailpit) -> None:
    """Baseline: the real integration's send() puts a message on the wire Mailpit accepts."""
    host, port, api = mailpit
    cfg = Settings(**_config(host, port)).smtp

    # send returns None on success and raises EmailSendError on failure
    await EmailIntegration(cfg).send(
        to=["ops@plant.example"], subject="Tank 3 overflow", body="Level exceeded threshold")

    messages = _messages(api)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["Subject"] == "Tank 3 overflow"
    assert [r["Address"] for r in msg["To"]] == ["ops@plant.example"]
    assert msg["From"]["Address"] == _FROM


async def test_action_send_delivered_ack_end_to_end(mailpit) -> None:
    """The decisive path: a real CustomAction -> on_custom_action -> real send -> success ack,
    with Mailpit confirming the subject/from/to AND the body actually crossed the wire."""
    host, port, api = mailpit
    async with KelvinAppTest(main.app, manifest=_manifest(host, port)) as harness:
        await harness.publish(
            _action("ops@plant.example", "Compressor 7 tripped", "Discharge pressure over limit"))
        await _await_ack(harness)

        assert len(harness.outputs) == 1
        ack = harness.outputs[0].payload
        assert ack.success is True
        assert ack.message == "Email sent"

    messages = _messages(api)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["Subject"] == "Compressor 7 tripped"
    assert msg["From"]["Address"] == _FROM
    assert [r["Address"] for r in msg["To"]] == ["ops@plant.example"]
    # Body only shows up on the single-message endpoint, not the list.
    body = _message(api, msg["ID"])["Text"]
    assert "Discharge pressure over limit" in body


async def test_smtp_rejection_acks_failure(mailpit_auth) -> None:
    """An auth-required server + an unauthenticated app config -> 530 -> EmailSendError -> failed ack."""
    host, port, _ = mailpit_auth
    async with KelvinAppTest(main.app, manifest=_manifest(host, port)) as harness:
        await harness.publish(_action("ops@plant.example", "Should be rejected", "no auth here"))
        await _await_ack(harness)

        assert len(harness.outputs) == 1
        ack = harness.outputs[0].payload
        assert ack.success is False
        # The failure ack carries the EmailSendError message verbatim, which wraps the SMTP error.
        assert ack.message.startswith("Failed to send email")
        assert "530" in ack.message


async def test_multiple_recipients_all_delivered(mailpit) -> None:
    """`to` as a list of addresses delivers to every recipient."""
    host, port, api = mailpit
    async with KelvinAppTest(main.app, manifest=_manifest(host, port)) as harness:
        await harness.publish(
            _action(["ops@plant.example", "maintenance@plant.example"], "Line 2 down", "restart needed"))
        await _await_ack(harness)

        assert harness.outputs[0].payload.success is True

    messages = _messages(api)
    assert len(messages) == 1
    to_addresses = {r["Address"] for r in messages[0]["To"]}
    assert to_addresses == {"ops@plant.example", "maintenance@plant.example"}


async def test_unicode_subject_and_body_round_trip(mailpit) -> None:
    """Non-ASCII subject/body survive the wire: RFC 2047 header + UTF-8 body decode back intact."""
    host, port, api = mailpit
    subject = "Válvula não fechou — 圧力 alto ⚠"
    body = "Nível acima do limite: 42°C — 温度が高すぎます. Ação imediata requerida."

    async with KelvinAppTest(main.app, manifest=_manifest(host, port)) as harness:
        await harness.publish(_action("ops@plant.example", subject, body))
        await _await_ack(harness)

        assert harness.outputs[0].payload.success is True

    messages = _messages(api)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["Subject"] == subject
    assert body in _message(api, msg["ID"])["Text"]


async def test_tls_cert_failure_acks_failure(mailpit) -> None:
    """use_tls=True against Mailpit's self-signed cert: the client's cert validation rejects it,
    which surfaces as a failed ack. Assert only on the ack, not the ssl error text (that's brittle)."""
    host, port, _ = mailpit
    async with KelvinAppTest(main.app, manifest=_manifest(host, port, use_tls=True)) as harness:
        await harness.publish(_action("ops@plant.example", "TLS attempt", "should not arrive"))
        await _await_ack(harness)

        assert len(harness.outputs) == 1
        ack = harness.outputs[0].payload
        assert ack.success is False
        assert ack.message.startswith("Failed to send email")
