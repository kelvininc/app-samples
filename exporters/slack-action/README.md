# Slack Action
This application demonstrates the use of the Kelvin SDK for handling custom actions.

It listens for `Slack Message` custom actions and sends Slack messages to a given channel.

**Only public channels are supported.** The app resolves the channel by name via `conversations_list`
(public channels only) and joins it on demand; private channels, DMs, and group DMs won't be found.

## Architecture Diagram

![Architecture](./assets/architecture-diagram.jpg)

## Failure Behavior
- **Invalid configuration** (missing or unresolved bot token) terminates the app at connect with
  exit code 1, with the validation errors logged; it never starts half-configured.
- **Malformed payloads** (missing/empty `channel` or `message.text`) and **send failures** (unknown
  channel, Slack API or network error) each ack the action with `success: false` and an
  operator-readable reason.
- Slack API calls time out after **10 seconds**; a timeout is acked as a failure like any other error.
- An action dispatched **before the app finishes connecting** is acked with `success: false` and a
  retry hint instead of crashing the handler.

## Slack App Setup

Set up a Slack app with the scopes this exporter needs:

### 1. Create a Slack App

1. Go to the [Slack API Apps Dashboard](https://api.slack.com/apps)
2. Click **"Create New App"**
3. Choose **"From scratch"**
4. Name your app (e.g. `MessageBot`) and select the workspace
5. Click **"Create App"**

### 2. Configure OAuth Scopes

1. In the left-hand menu, go to **"OAuth & Permissions"**
2. Under **Bot Token Scopes**, click **"Add an OAuth Scope"**
3. Add the following scopes:

| Scope           | Description                                                   |
|------------------|--------------------------------------------------------------|
| `channels:read`  | View basic information about public channels in a workspace  |
| `channels:join`  | Join public channels in a workspace                          |
| `chat:write`     | Send messages                                                |

These three scopes are all the app needs; it only works with **public channels**.

### 3. Install the App to Your Workspace

1. Still in **OAuth & Permissions**, under **OAuth Tokens**
2. Click **"Install App to Workspace"**
3. Copy the **Bot User OAuth Token** (e.g. `xoxb-...`) as it's going to be used to configure your Slack Message Sender instance

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and passes it through as `app_configuration`. The bot token lives under the
`slack` block.

1. Create `config.yaml` in the app root:
    ```yaml
    slack:
      token: "xoxb-your-bot-user-oauth-token"
    ```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** by publishing a `Slack Message` action:
   `kelvin app test generator --entrypoint tests/generator.py:CustomActionGenerator`

   The generator posts to a placeholder channel (`your-channel-name` in `tests/generator.py`);
   change it to a public channel that exists in your workspace before running.

The action payload is `{ "channel": "<public-channel-name>", "message": { "text": "<required>" } }`.

## Test Locally

### Unit Tests
Everything runs locally; no Docker, no Slack workspace:

```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps
pytest                                          # unit + harness tests
```

- **Unit** (`test_settings.py`, `test_slack_integration.py`): settings validation, and
  `SlackIntegration` (channel lookup, join-on-demand, error mapping) against a faked Slack
  web client.
- **Harness** (`test_main.py`): the custom-action to integration to result flow via `KelvinAppTest`.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, the bot token must be a **Secret**, referenced from the deployment configuration
with `<% secrets.<name> %>`.

    1. Create the secret:
        ```
        kelvin secret create slack-bot-token --value "<bot-user-oauth-token>"
        ```

    2. Reference it from the deployment configuration:
        ```yaml
        slack:
          token: "<% secrets.slack-bot-token %>"
        ```

    > The unresolved `<% secrets... %>` literal is normalized to unset by the settings validator,
    > so a deployment that forgot to wire the secret fails fast at connect (the token is required).
