# Microsoft Teams Exporter
This application demonstrates the use of the Kelvin SDK for handling custom actions.

It listens for `Teams Message` custom actions and posts messages to Microsoft Teams channels
via Incoming Webhooks. Each action names a channel; the exporter posts to that channel's webhook.

## Failure Behavior
- **Invalid configuration** (an empty webhook list, a blank channel label, two entries sharing a
  label, a missing/blank webhook URL, a URL that is not `http(s)`, or an unresolved secret)
  terminates the app at connect with exit code 1; it never starts half-configured. The rejection
  reason never includes the URL, which embeds a token.
- **Malformed payload** (e.g. no `channel`, `message` is not an object, or `text` is missing,
  empty, or whitespace-only) acks the action with `success: false` and a message describing the
  invalid fields.
- **Unconfigured channel** acks the action with `success: false`. The lookup is local, so nothing
  is sent to Teams.
- **Webhook failures** (a 4xx/5xx response, a network error, or a timeout) ack the action with
  `success: false` and an operator-readable reason. HTTP requests are capped at **10 seconds**
  total, so a hung webhook fails the action instead of stalling it.

There is no buffering and no retry: a failed send is reported back immediately and the action
is complete.

## Teams Setup

A Teams **Incoming Webhook** URL is bound to the channel it was created in. The HTTP request
carries no destination, so posting to N channels means creating N webhooks and configuring one
entry per channel. The `channel` in the action payload picks between those entries; it's a local
label that never reaches Teams, and nothing verifies it names the channel the webhook posts to.

Treat webhook URLs as credentials: anyone holding one can post to the channel.

### Create the Webhook (Power Automate "Workflows", Current Method)

1. In the target Teams channel, open **··· → Workflows**.
2. Choose the template **"Post to a channel when a webhook request is received"**.
3. Confirm the Team and Channel the workflow posts to; they're fixed when the flow is created.
4. Complete the wizard and copy the generated **HTTP POST URL**. That URL is the secret this app needs.
5. Repeat for every channel you want to reach.

This app posts an **Adaptive Card** (the body Workflows webhooks expect). Tenants still on the
legacy **Incoming Webhook connector** accept a flat `MessageCard` instead; the POST mechanism is
identical, only the JSON body differs (see `teams_integration.build_card`). Those connectors are
one-channel too.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform
injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`);
the SDK reads it and passes it through as `app_configuration`. The webhooks live under the
`teams` block.

1. Create `config.yaml` in the app root:
    ```yaml
    teams:
      webhooks:
        - channel: alerts
          url: "https://<your-org>.webhook.office.com/webhookb2/..."
    ```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** by publishing a `Teams Message` action:
   `kelvin app test generator --entrypoint tests/generator.py:CustomActionGenerator`

   The generator posts to the `alerts` channel (in `tests/generator.py`); change it to match a
   channel in your `config.yaml`.

The action payload is
`{ "channel": "<required>", "title": "<optional>", "message": { "text": "<required>" } }`.
Channel matching ignores case and surrounding whitespace.

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
  (required/blank/unresolved URLs, blank and duplicate channels) and the webhook client against
  a faked aiohttp session (channel routing, status codes, timeouts, card layout).

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, every webhook URL must be a **Secret**, referenced from the
deployment configuration with `<% secrets.<name> %>`.

    1. Create one secret per channel:
        ```
        kelvin secret create teams-webhook-alerts --value "<webhook-url>"
        ```

    2. Reference them from the deployment configuration:
        ```yaml
        teams:
          webhooks:
            - channel: alerts
              url: "<% secrets.teams-webhook-alerts %>"
            - channel: ops
              url: "<% secrets.teams-webhook-ops %>"
        ```

    > The unresolved `<% secrets... %>` literal is normalized to unset by the settings validator,
    > so a deployment that forgot to wire a secret fails fast at connect (every URL is required).
