# Microsoft Teams Sender

This application demonstrates the use of the Kelvin SDK for handling custom actions.

The application listens for `Teams Message` custom actions and sends the message to a Microsoft
Teams channel. Each configured channel maps to a Teams incoming webhook (a Power Automate
workflow URL); the action payload names the channel, the exporter looks up its webhook and posts
the message as an Adaptive Card.

Its companion is the [Teams Message Test](../../applications/teams-message-test/) SmartApp, which
publishes a `Teams Message` custom action every minute. Deploy both to exercise the flow end to
end.

## Failure Behavior

Every `Teams Message` action is acknowledged with a `CustomActionResult`, so the platform always
sees the outcome:

- **Success** when the webhook accepts the message (HTTP 2xx).
- **Failure**, with the reason in the result message, when the payload is missing `channel` or
  `message.text`, when no webhook is configured for the requested channel, when the webhook
  secret was never created on the deployment, or when the webhook rejects the request (HTTP
  error, network failure, timeout).

There is no buffering or retry: a failed send is reported back immediately and the action is
complete.

## Microsoft Teams Setup

Teams no longer supports the classic Office 365 connector webhooks; incoming webhooks are created
with a Power Automate workflow instead. For each channel you want the exporter to post to:

1. In Microsoft Teams, open the channel, click **⋯ → Workflows**, and pick the template
   **"Post to a channel when a webhook request is received"** (also available from the
   [Power Automate portal](https://make.powerautomate.com)).
2. Confirm the **Team** and **Channel** the workflow posts to and create the workflow.
3. Copy the generated **HTTP POST URL** — this is the webhook URL the exporter needs.
4. Repeat for every channel; each webhook URL posts to exactly one channel.

Treat webhook URLs as credentials: anyone who has one can post to the channel.

## Prerequisites

1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally

Configuration is read from `app.app_configuration`, the same nested structure the platform
injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`);
the SDK reads it and passes it through as `app_configuration`.

1. Create `config.yaml` in the app root:
    ```yaml
    webhooks:
      - channel: test
        url: https://<your-workflow-url>
    ```
2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** with generator: `kelvin app test generator --entrypoint tests/generator.py:CustomActionGenerator`

## Test Locally

### Unit Tests

```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps
pytest                                           # fast, no Docker
```

The default run covers the settings model (unresolved secrets, unknown channels, platform-injected
keys), the Adaptive Card payload the integration posts, the HTTP and network failure paths, and
the full action-to-result flow through the Kelvin test harness — no real Teams webhook needed.

## Kelvin Cloud Deployment

1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: set the same config on the deployment instead of a local `config.yaml`, and
   wire every webhook URL as a **Secret**, referenced with `<% secrets.<name> %>`.
    ```
    kelvin secret create teams-webhook-test --value "<workflow_url>"
    ```
    ```yaml
    webhooks:
      - channel: test
        url: <% secrets.teams-webhook-test %>
    ```

> Wire credentials only via `<% secrets... %>`; leave them out of `app.yaml` defaults so a
> baked-in placeholder can't be mistaken for a real credential.
