# Email Exporter
This application demonstrates the use of the Kelvin SDK for handling custom actions.

It listens for `Email Message` custom actions and sends emails through an SMTP server.

## Failure Behavior
- **Invalid configuration terminates the app** at connect (exit code 1) with the validation errors
  logged, so a misconfigured deployment fails visibly instead of silently dropping actions.
- **Malformed payloads** (a `to`, `subject`, or `message.text` that is missing, empty, or
  whitespace-only, or a recipient that is not a valid email address) and **send failures** (SMTP
  errors, network errors, invalid headers) each publish a failure ack (`CustomActionResult` with
  `success: false` and the reason) back to the platform.
- SMTP operations time out after **30 seconds**, so a hung server can't stall the message loop
  indefinitely.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and passes it through as `app_configuration`. SMTP settings live under the `smtp` block.

1. Create `config.yaml` in the app root:
    ```yaml
    smtp:
      host: "smtp.example.com"
      port: 587                  # STARTTLS
      use_tls: true
      from_address: "alerts@example.com"
      auth:
        method: username_password
        username: "alerts@example.com"
        password: "<app-password>"
        # method: none           # unauthenticated relay; no credentials needed
    ```

`from_address` is validated as an email address, like the recipients in the action payload, so a
typo fails at connect instead of reaching the relay. The same domain rule applies: a bare hostname
(`alerts@mailrelay`) is rejected. A display name (`"Plant Alerts <alerts@example.com>"`) is
accepted but reduced to the bare address, so set the sender's display name on the relay if you
need one.

### SMTP Authentication
`smtp.auth.method` picks the mechanism explicitly:

- **`none`** (the default): unauthenticated send, for an internal relay that trusts the network.
  No credentials are sent, even if `username`/`password` happen to be present in the config.
- **`username_password`**: SMTP AUTH; **both** `username` and `password` are required, and config
  validation fails at connect if either is missing.

> **Upgrading from 1.x/2.0:** auth was previously inferred from credential presence; existing
> deployments must now set `auth.method` explicitly (`username_password` if they had credentials).

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** by publishing an `Email Message` action with `kelvin app test`.

The action payload is `{ "to": "a@x.com" | ["a@x.com", "b@x.com"], "subject": "...", "message": { "text": "..." } }`.

`to` is either a single address or a list of them; a multi-address string like `"a@x.com,b@x.com"`
is **not** supported and fails as one malformed address. Every entry is validated, so `to` requires
well-formed addresses with a globally deliverable domain: bare hostnames (`root@mailrelay`) and IP
literals (`ops@10.0.0.1`) are rejected even though an internal relay would accept them. An empty
list (`[]`) is the only empty form that validates, and it fails the recipients check instead. The
domain is lowercased on the way through.

## Test Locally
Two layers, gated so the default run needs no Docker:

### Unit Tests
```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps
pytest                                           # unit + harness tests (fast, no Docker)
```

- **Unit** (`tests/test_settings.py`, `tests/test_email_integration.py`): config validation (auth
  methods, un-wired secrets, ports/blanks) and `EmailIntegration` against a faked `aiosmtplib.send`.
- **Harness** (`tests/test_main.py`): recipient normalization and the custom-action → integration →
  result flow via `KelvinAppTest`.

### Integration Tests
```bash
pip install testcontainers                       # real-server deps
pytest -m integration                            # smoke test against a live Mailpit server (Docker required)
```

- **Integration** (`tests/test_integration.py`, marker-gated, deselected by default): boots an
  `axllent/mailpit` container, sends a real email through `EmailIntegration` (aiosmtplib), and queries
  Mailpit's API to assert the message arrived with the right subject, sender, and recipient.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, the SMTP credentials must be **Secrets**, referenced from the deployment configuration with
`<% secrets.<name> %>`. The host/port/from address are not sensitive and can be set directly.

    1. Create the secrets:
        ```
        kelvin secret create smtp-username --value "<username>"
        kelvin secret create smtp-password --value "<password>"
        ```

    2. Reference them from the deployment configuration:
        ```yaml
        smtp:
          host: "smtp.example.com"
          port: 587
          from_address: "alerts@example.com"
          auth:
            method: username_password
            username: "<% secrets.smtp-username %>"
            password: "<% secrets.smtp-password %>"
        ```

    > An unresolved `<% secrets... %>` literal (username or password) is normalized to unset by the
    > settings validator, and `method: username_password` requires both credentials, so an un-wired
    > secret always fails config validation and the app exits at connect, rather than causing a
    > confusing auth failure later.
