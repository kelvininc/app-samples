# Microsoft Teams Action
This application demonstrates the use of the Kelvin SDK for handling custom actions.

It listens for `Teams Message` custom actions and posts messages to a Microsoft
Teams channel via an Incoming Webhook.

## Failure Behavior
- **Invalid configuration** (missing/blank webhook URL, unresolved secret) terminates the app at
  connect with exit code 1; it never starts half-configured.
- **Malformed payload** (e.g. `message` is not an object, or `text` is missing/empty) acks the
  action with `success: false` and a message describing the invalid fields.
- **Webhook failures** (a 4xx/5xx response, a network error, or a timeout) ack the action with
  `success: false` and an operator-readable reason. HTTP requests are capped at **10 seconds**
  total, so a hung webhook fails the action instead of stalling it.

## Teams Setup

This exporter posts to a Teams **Incoming Webhook** URL; the webhook is bound to one channel,
so there's no channel selection in the action (the URL *is* the destination).

### Create the Webhook (Power Automate "Workflows", Current Method)

1. In the target Teams channel, open **··· → Workflows**.
2. Choose the template **"Post to a channel when a webhook request is received"**.
3. Complete the wizard and copy the generated **HTTP POST URL**. That URL is the secret this app needs.

This app posts an **Adaptive Card** (the body Workflows webhooks expect). Tenants still on the
legacy **Incoming Webhook connector** accept a flat `MessageCard` instead; the POST mechanism is
identical, only the JSON body differs (see `teams_integration.build_card`).

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform
injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`);
the SDK reads it and passes it through as `app_configuration`. The webhook URL lives under the
`teams` block.

1. Create `config.yaml` in the app root:
    ```yaml
    teams:
      webhook_url: "https://<your-org>.webhook.office.com/webhookb2/..."
    ```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** by publishing a `Teams Message` action with `kelvin app test`.

The action payload is `{ "title": "<optional>", "message": { "text": "<required>" } }`.

## Test Locally

### Unit Tests
All tests run locally with no network and no Docker:

```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps
pytest                                           # unit + harness tests
```

- **Harness** (`tests/test_main.py`): the custom-action → integration → result flow via
  `KelvinAppTest`: success and failure acks, payload validation, the startup race, and the
  fatal-config exit.
- **Unit** (`tests/test_settings.py`, `tests/test_teams_integration.py`): settings validation
  (required/blank/unresolved webhook URL) and the webhook client against a faked aiohttp
  session (status codes, timeouts, card layout).

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, the webhook URL must be a **Secret**, referenced from the deployment configuration
with `<% secrets.<name> %>`.

    1. Create the secret:
        ```
        kelvin secret create teams-webhook-url --value "<webhook-url>"
        ```

    2. Reference it from the deployment configuration:
        ```yaml
        teams:
          webhook_url: "<% secrets.teams-webhook-url %>"
        ```

    > The unresolved `<% secrets... %>` literal is normalized to unset by the settings validator,
    > so a deployment that forgot to wire the secret fails fast at connect (the URL is required).
