from typing import Optional

from kelvin.application import KelvinApp
from kelvin.logs import logger
from kelvin.message import CustomAction
from pydantic import BaseModel, Field, ValidationError

from settings import Settings
from teams_integration import TeamsIntegration, TeamsSendError

app = KelvinApp()
_integration: Optional[TeamsIntegration] = None    # built once in on_connect

_ACTION_TYPE = "teams message"           # the custom action this app handles (declared in app.yaml)


class _MessageBody(BaseModel):
    text: str = Field(min_length=1)


class TeamsPayload(BaseModel):
    """The 'Teams Message' action payload: a required message body and an optional card title."""

    message: _MessageBody
    title: Optional[str] = None


def _format_errors(e: ValidationError) -> str:
    """Render a ValidationError as 'loc: msg' pairs, without URLs or input echoes."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" if err["loc"] else err["msg"]
        for err in e.errors(include_url=False, include_input=False)
    )


@app.on_connect
async def on_connect() -> None:
    """Validate config and build the integration; an invalid configuration is fatal."""
    global _integration
    try:
        settings = Settings(**app.app_configuration)
    except ValidationError as e:
        logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
        # SystemExit(1) exits cleanly with code 1 and no traceback. The SDK re-raises
        # on_connect exceptions (unlike message-handler errors, which the read loop
        # swallows), so a bare raise would also crash; SystemExit makes the intent explicit.
        raise SystemExit(1)
    # One integration (and one pooled HTTP session) for the app's lifetime.
    _integration = TeamsIntegration(settings.teams)


@app.on_disconnect
async def on_disconnect() -> None:
    """Close the integration's HTTP session on shutdown."""
    global _integration
    if _integration is not None:
        await _integration.close()
        _integration = None


@app.on_custom_action
async def on_custom_action(action: CustomAction) -> None:
    """Handle a 'Teams Message' action: post its text to the configured channel and ack."""
    if action.type.lower() != _ACTION_TYPE:
        logger.warning("Received unexpected Custom Action", action=action)
        return

    try:
        payload = TeamsPayload.model_validate(action.payload)
    except ValidationError as e:
        logger.error("Invalid action payload", action=action, errors=e.errors(include_url=False, include_input=False))
        await app.publish(action.result(success=False, message=f"Invalid payload: {_format_errors(e)}"))
        logger.info("Finished handling Teams Message Action", success=False)
        return

    if _integration is None:
        # The SDK starts the read loop before awaiting on_connect, so an action buffered
        # alongside the manifest can be dispatched before the integration is built.
        await app.publish(action.result(success=False, message="Connector is still starting; retry shortly"))
        logger.info("Finished handling Teams Message Action", success=False)
        return

    try:
        await _integration.send_message(text=payload.message.text, title=payload.title)
    except TeamsSendError as e:
        await app.publish(action.result(success=False, message=str(e)))
        logger.info("Finished handling Teams Message Action", success=False)
        return

    await app.publish(action.result(success=True, message="Teams message sent"))
    logger.info("Finished handling Teams Message Action", success=True)


if __name__ == "__main__":
    app.run()
