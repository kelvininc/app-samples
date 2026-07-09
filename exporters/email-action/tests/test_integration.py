"""Real-server smoke test (a live Mailpit SMTP server via testcontainers; Docker required).

Excluded from the default suite; run with `pytest -m integration`. Drives the real `EmailIntegration`
(aiosmtplib over the wire) against a Mailpit container, then queries Mailpit's API to confirm the
message actually arrived; the SMTP handshake/delivery the unit tests fake.
"""
import json
import urllib.request

import pytest

from email_integration import EmailIntegration
from settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def mailpit():
    """Boot one Mailpit server for the module; yields (smtp_host, smtp_port, api_base_url)."""
    from testcontainers.mailpit import MailpitContainer

    with MailpitContainer() as c:
        yield c.get_container_host_ip(), c.get_exposed_smtp_port(), c.get_base_api_url()


def _messages(api_base_url: str) -> list[dict]:
    with urllib.request.urlopen(f"{api_base_url}/api/v1/messages", timeout=10) as resp:
        return json.load(resp)["messages"]


async def test_email_is_delivered(mailpit) -> None:
    host, port, api = mailpit
    cfg = Settings(smtp={"host": host, "port": port, "use_tls": False,
                         "from_address": "alerts@plant.example",
                         "auth": {"method": "none"}}).smtp

    # send returns None on success and raises EmailSendError on failure
    await EmailIntegration(cfg).send(
        to=["ops@plant.example"], subject="Tank 3 overflow", body="Level exceeded threshold")

    messages = _messages(api)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["Subject"] == "Tank 3 overflow"
    assert [r["Address"] for r in msg["To"]] == ["ops@plant.example"]
    assert msg["From"]["Address"] == "alerts@plant.example"
