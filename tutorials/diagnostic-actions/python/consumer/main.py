from kelvin.application import KelvinApp
from kelvin.message import CustomAction

app = KelvinApp()


@app.on_custom_action
async def inspect_request(action: CustomAction) -> None:
    """Echo a diagnostic request without contacting equipment."""
    if action.type != "run_diagnostics":
        return
    include_history = action.payload.get("include_history", False)
    if not isinstance(include_history, bool):
        await app.publish(
            action.result(success=False, message="include_history must be a Boolean")
        )
        return
    await app.publish(
        action.result(success=True, response={"history_requested": include_history})
    )


if __name__ == "__main__":
    app.run()
