from email.message import EmailMessage

import aiosmtplib
from kelvin.logs import logger

from settings import Smtp


class EmailSendError(Exception):
    """An expected, operator-reportable send failure; the message goes verbatim into the failure ack."""


class EmailIntegration:
    """Sends an email via SMTP. aiosmtplib is natively async, so calls are awaited directly."""

    def __init__(self, config: Smtp) -> None:
        self.config = config

    def _build(self, to: list[str], subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.config.from_address
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(body)
        return msg

    async def send(self, to: list[str], subject: str, body: str) -> None:
        """Send the email; returns None on success, raises EmailSendError on failure.

        ValueError covers CR/LF header injection rejected by EmailMessage; OSError covers
        network failures (DNS, refused connection, ...).
        """
        a = self.config.auth
        authenticated = a.method == "username_password"
        try:
            await aiosmtplib.send(
                self._build(to, subject, body),
                hostname=self.config.host,
                port=self.config.port,
                start_tls=self.config.use_tls,
                username=a.username if authenticated else None,
                password=a.password.get_secret_value() if authenticated and a.password else None,
                timeout=30,
            )
        except (aiosmtplib.SMTPException, ValueError, OSError) as e:
            logger.error("Failed to send email", error=str(e))
            raise EmailSendError(f"Failed to send email ({e})") from e
        logger.info("Email sent", to=to, subject=subject)
