# SFTP Exporter
This application demonstrates the use of the Kelvin SDK for uploading streaming data to an SFTP server.

Incoming streaming data is buffered in a local DuckDB file, then drained in batches, streamed to a
Parquet/CSV/JSON file, and uploaded to a remote SFTP directory. Number, string, and boolean values
keep their native types through a type-preserving scalar-JSON payload column.

## Delivery Semantics
Delivery is **at-least-once**: the buffer is trimmed only after a confirmed upload. Files are
named `<remote_dir>/batch-<utc-timestamp>-<cursor>.<format>` with the timestamp generated at upload
time, so a retried upload (for example after a crash between the upload and the buffer trim) can
write the same batch under two different names. Consumers must tolerate duplicate rows.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on
deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and
passes it through as `app_configuration`.

1. Create `config.yaml` in the app root:
    ```yaml
    sftp:
      host: "<host>"
      port: 22
      username: "<username>"
      remote_dir: "/incoming"          # must already exist on the server
      timeout: 30                      # network timeout (s) for connect/auth/transfers
      verify_host_key: true            # set false to auto-accept unknown keys (dev only; MITM risk)
      # known_hosts: "/path/to/known_hosts"
      auth:
        method: password               # or "private_key"
        password: "<password>"
        # method: private_key
        # private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----"
        # private_key_passphrase: "<passphrase>"
    upload:
      interval: 60
      batch_size: 1000
      format: parquet                  # "parquet" | "csv" | "json"
    buffer:
      max_backlog: 0
    ```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** with synthetic data: `kelvin app test simulator`

> **Host-key verification** is on by default: the server's key is checked against `known_hosts` (the path
> you set, or the system file) and an unknown key is rejected (anti-MITM). Set `verify_host_key: false` to
> auto-accept unknown keys; convenient for a first run, but unsafe in production.

## Test Locally
Two layers, gated so the default run needs no Docker.

### Unit Tests
```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps
pytest                                           # unit tests (fast, no Docker)
```

`test_*.py` cover store/drain/writer logic, settings validation, and host-key policy selection (no network).

### Integration Tests
```bash
pip install testcontainers                       # real-server deps
pytest -m integration                            # smoke tests against a live SFTP server (Docker required)
```

`test_integration.py` boots an `atmoz/sftp` container and drives the real `SftpWriter`: it uploads a batch
with the server's key in `known_hosts` and asserts the file lands, and confirms the default
(`verify_host_key=True`) rejects an unknown host. The suite is marker-gated, deselected by default, and
requires Docker.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, the same `sftp` / `upload` / `buffer` configuration is set on the deployment. Credentials
must be **Secrets**; the non-sensitive fields can be set directly.

    ```
    kelvin secret create sftp-password --value "<password>"
    # or, for key auth:
    kelvin secret create sftp-private-key --value "<pem-private-key>"
    ```

    Reference the secret(s) from the deployment configuration with `<% secrets.<name> %>`:

    ```yaml
    sftp:
      host: "<host>"
      username: "<username>"
      remote_dir: "/incoming"
      auth:
        method: password
        password: "<% secrets.sftp-password %>"
    ```

    > Wire credentials only via `<% secrets... %>`; leave them out of `app.yaml` defaults so a baked-in
    > placeholder can't be mistaken for a real credential (it would defeat the one-auth validation).

### Host Key Verification
The container image has no `~/.ssh/known_hosts`, so with `verify_host_key: true` (the default) you must
deploy a `known_hosts` file with the app; the writer fails setup with a clear error if none is loaded:

1. Capture the server's key and verify its fingerprint out-of-band (e.g. against what the server admin
   publishes) before trusting it:
   ```
   ssh-keyscan -H <host> > known_hosts
   ```
2. Attach the file to the deployment as a platform **text volume**, mounted at e.g.
   `/opt/kelvin/share/known_hosts`.
3. Point the configuration at it:
   ```yaml
   sftp:
     known_hosts: "/opt/kelvin/share/known_hosts"
   ```

If the server's host key is ever rotated, uploads stop with a host-key rejection until you update the
file with the new key.
