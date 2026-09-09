"""Unit tests for EmailIntegration against a faked aiosmtplib.send (no network).

Contract: `send` returns None on success and raises EmailSendError on any expected failure
(SMTP protocol errors, header-injection ValueErrors, network OSErrors).
"""
import aiosmtplib
import pytest

import email_integration as ei
from email_integration import EmailIntegration, EmailSendError
from settings import Smtp

pytestmark = pytest.mark.asyncio

SMTP = {"host": "smtp.example.com", "from_address": "alerts@example.com"}


def _patch(monkeypatch: pytest.MonkeyPatch, raise_exc: Exception | None = None) -> dict:
    """Replace aiosmtplib.send with a recorder; return the captured (msg, kwargs)."""
    captured: dict = {}

    async def fake_send(msg, **kwargs):
        captured["msg"], captured["kwargs"] = msg, kwargs
        if raise_exc:
            raise raise_exc
        return ({}, "OK")

    monkeypatch.setattr(ei.aiosmtplib, "send", fake_send)
    return captured


async def test_sends_with_headers_starttls_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful send returns None, builds the headers, and uses host/port/STARTTLS/timeout."""
    captured = _patch(monkeypatch)
    cfg = Smtp(**SMTP)
    result = await EmailIntegration(cfg).send(["a@x.com", "b@x.com"], "Pump alert", "temp high")
    assert result is None
    msg, kwargs = captured["msg"], captured["kwargs"]
    assert msg["To"] == "a@x.com, b@x.com" and msg["From"] == "alerts@example.com"
    assert msg["Subject"] == "Pump alert" and "temp high" in msg.get_content()
    assert kwargs["hostname"] == "smtp.example.com" and kwargs["port"] == 587 and kwargs["start_tls"] is True
    assert kwargs["timeout"] == 30


async def test_passes_credentials_for_username_password_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """With method='username_password', the credentials are forwarded (unwrapped)."""
    captured = _patch(monkeypatch)
    cfg = Smtp(**SMTP, auth={"method": "username_password", "username": "u", "password": "shhh"})
    await EmailIntegration(cfg).send(["a@x.com"], "s", "b")
    assert captured["kwargs"]["username"] == "u" and captured["kwargs"]["password"] == "shhh"


async def test_omits_credentials_for_method_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the default method='none', username/password are None (unauthenticated relay)."""
    captured = _patch(monkeypatch)
    await EmailIntegration(Smtp(**SMTP)).send(["a@x.com"], "s", "b")
    assert captured["kwargs"]["username"] is None and captured["kwargs"]["password"] is None


async def test_method_none_suppresses_stray_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """method='none' never sends credentials, even if they are present in the config."""
    captured = _patch(monkeypatch)
    cfg = Smtp(**SMTP, auth={"method": "none", "username": "u", "password": "shhh"})
    await EmailIntegration(cfg).send(["a@x.com"], "s", "b")
    assert captured["kwargs"]["username"] is None and captured["kwargs"]["password"] is None


async def test_smtp_error_raises_email_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An SMTP protocol error is wrapped in EmailSendError, chaining the cause."""
    _patch(monkeypatch, raise_exc=aiosmtplib.SMTPException("relay refused"))
    with pytest.raises(EmailSendError, match="relay refused") as exc_info:
        await EmailIntegration(Smtp(**SMTP)).send(["a@x.com"], "s", "b")
    assert isinstance(exc_info.value.__cause__, aiosmtplib.SMTPException)


async def test_header_injection_raises_email_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CR/LF subject is rejected by EmailMessage (ValueError) and surfaces as EmailSendError."""
    _patch(monkeypatch)
    with pytest.raises(EmailSendError, match="Failed to send email"):
        await EmailIntegration(Smtp(**SMTP)).send(["a@x.com"], "s\r\nBcc: evil@x.com", "b")


async def test_network_error_raises_email_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network failure (OSError) surfaces as EmailSendError."""
    _patch(monkeypatch, raise_exc=OSError("connection refused"))
    with pytest.raises(EmailSendError, match="connection refused"):
        await EmailIntegration(Smtp(**SMTP)).send(["a@x.com"], "s", "b")
