from typing import Optional

from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import CustomAction
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError

from email_integration import EmailIntegration, EmailSendError
from settings import Settings

app = KelvinApp()
_integration: Optional[EmailIntegration] = None  # built once in on_connect

_ACTION_TYPE = "email message"           # the custom action this app handles (declared in app.yaml)


class _MessageBody(BaseModel):
    # str_strip_whitespace so a whitespace-only text fails min_length instead of being sent as-is.
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1)


class EmailPayload(BaseModel):
    """The 'Email Message' action payload: recipients, subject, and message body."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # One address or a list of them; nothing else. Every entry is validated, so a typo, a bare
    # hostname (root@mailrelay), or an IP literal is rejected rather than handed to the relay.
    to: EmailStr | list[EmailStr]
    subject: str = Field(min_length=1)
    message: _MessageBody

    @property
    def recipients(self) -> list[str]:
        """`to` as a list, whether it arrived as a single address or a list of them."""
        return [self.to] if isinstance(self.to, str) else list(self.to)


def _format_errors(e: ValidationError) -> str:
    """Render a ValidationError as 'loc: msg' pairs, without URLs or input echoes.

    A union like `EmailStr | list[EmailStr]` tags each branch in the error location, which renders
    as pydantic internals ("to.function-after[_validate(), str]"). Those tags are dropped so the
    ack names the field the caller actually sent: "to" or "to.1". A field name can never contain a
    bracket, so this only ever strips generated tags.
    """
    return "; ".join(
        f"{'.'.join(loc)}: {err['msg']}" if (loc := _clean_loc(err["loc"])) else err["msg"]
        for err in e.errors(include_url=False, include_input=False)
    )


def _clean_loc(loc: tuple) -> list[str]:
    """Drop pydantic's union branch tags from an error location."""
    return [str(part) for part in loc if not (isinstance(part, str) and "[" in part)]


@app.on_connect
async def on_connect() -> None:
    """Validate config and build the email integration; a bad config is fatal at connect."""
    global _integration
    try:
        settings = Settings(**app.app_configuration)
    except ValidationError as e:
        logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
        # SystemExit(1) exits cleanly with code 1 and no traceback. The SDK re-raises
        # on_connect exceptions (unlike message-handler errors, which the read loop
        # swallows), so a bare raise would also crash; SystemExit makes the intent explicit.
        raise SystemExit(1)
    _integration = EmailIntegration(settings.smtp)


@app.on_custom_action
async def on_custom_action(action: CustomAction) -> None:
    """Handle an 'Email Message' action: send the message to the recipients and ack."""
    if action.type.lower() != _ACTION_TYPE:
        logger.warning("Received unexpected Custom Action", action=action)
        return

    try:
        payload = EmailPayload.model_validate(action.payload)
    except ValidationError as e:
        logger.error("Invalid action payload", action=action, errors=e.errors(include_url=False, include_input=False))
        await app.publish(action.result(success=False, message=f"Invalid payload: {_format_errors(e)}"))
        logger.info("Finished handling Email Message Action", success=False)
        return

    if _integration is None:
        # The SDK starts the read loop before awaiting on_connect, so an action buffered
        # alongside the manifest can be dispatched before the integration is built.
        await app.publish(action.result(success=False, message="Connector is still starting; retry shortly"))
        logger.info("Finished handling Email Message Action", success=False)
        return

    if not payload.recipients:
        logger.error("No recipients ('to') specified in the action payload", action=action)
        await app.publish(action.result(success=False, message="No recipients ('to') specified"))
        logger.info("Finished handling Email Message Action", success=False)
        return

    try:
        await _integration.send(payload.recipients, payload.subject, payload.message.text)
    except EmailSendError as e:
        await app.publish(action.result(success=False, message=str(e)))
        logger.info("Finished handling Email Message Action", success=False)
        return

    await app.publish(action.result(success=True, message="Email sent"))
    logger.info("Finished handling Email Message Action", success=True)


if __name__ == "__main__":
    app.run()
