from typing import Optional

from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import CustomAction
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from settings import Settings
from slack_integration import SlackIntegration, SlackSendError

app = KelvinApp()
_integration: Optional[SlackIntegration] = None    # built once in on_connect, reused across actions

_ACTION_TYPE = "slack message"                     # the custom action this app handles (declared in app.yaml)


class _MessageBody(BaseModel):
    # str_strip_whitespace so a whitespace-only text fails min_length instead of being sent as-is.
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1)


class SlackPayload(BaseModel):
    """The 'Slack Message' action payload: a target public channel and the message body."""

    model_config = ConfigDict(str_strip_whitespace=True)

    channel: str = Field(min_length=1)
    message: _MessageBody


def _format_errors(e: ValidationError) -> str:
    """Render a ValidationError as 'loc: msg' pairs, without URLs or input echoes."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" if err["loc"] else err["msg"]
        for err in e.errors(include_url=False, include_input=False)
    )


@app.on_connect
async def on_connect() -> None:
    """Validate config and build the Slack integration; a bad config is fatal at connect."""
    global _integration
    try:
        settings = Settings(**app.app_configuration)
    except ValidationError as e:
        logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
        # SystemExit(1) exits cleanly with code 1 and no traceback. The SDK re-raises
        # on_connect exceptions (unlike message-handler errors, which the read loop
        # swallows), so a bare raise would also crash; SystemExit makes the intent explicit.
        raise SystemExit(1)

    # One integration for the app's lifetime: it holds the Slack client and the channel cache.
    _integration = SlackIntegration(settings.slack)


@app.on_custom_action
async def on_custom_action(action: CustomAction) -> None:
    """Handle a 'Slack Message' action: post its text to the requested channel and ack."""
    if action.type.lower() != _ACTION_TYPE:
        logger.warning("Received unexpected Custom Action", action=action)
        return

    try:
        payload = SlackPayload.model_validate(action.payload)
    except ValidationError as e:
        logger.error("Invalid action payload", action=action, errors=e.errors(include_url=False, include_input=False))
        await app.publish(action.result(success=False, message=f"Invalid payload: {_format_errors(e)}"))
        logger.info("Finished handling Slack Message Action", success=False)
        return

    if _integration is None:
        # The SDK starts the read loop before awaiting on_connect, so an action buffered
        # alongside the manifest can be dispatched before the integration is built.
        await app.publish(action.result(success=False, message="Connector is still starting; retry shortly"))
        logger.info("Finished handling Slack Message Action", success=False)
        return

    try:
        await _integration.send_message(payload.channel, payload.message.text)
    except SlackSendError as e:
        await app.publish(action.result(success=False, message=str(e)))
        logger.info("Finished handling Slack Message Action", success=False)
        return

    await app.publish(action.result(success=True, message="Slack message sent"))
    logger.info("Finished handling Slack Message Action", success=True)


if __name__ == "__main__":
    app.run()
