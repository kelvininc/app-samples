# MQTT Importer
This application demonstrates the use of the Kelvin SDK for importing data from an MQTT broker.

A bidirectional MQTT connector. It subscribes to the topics mapped to each Kelvin datastream,
publishes received payloads into Kelvin, and writes Kelvin control changes back out to the broker.

Topic-to-stream mapping is **operator configuration**: an operator maps each Kelvin asset/stream to
an MQTT topic in the Kelvin UI (the `io_configuration` schema). The connector reads that mapping from
`app.assets[...].datastreams[...].configuration` when it connects to the broker. Configuration or
mapping changes require redeploying the workload (the platform restarts it), because a healthy broker
connection never rebuilds its maps. The stream's **declared data type** drives how payloads are
decoded; there is no separate `primitive` to keep in sync.

## Capabilities

- **Topics**: literal, MQTT `+`/`#` wildcards, and `{asset}`/`{stream}` placeholders that expand per
  deployed stream (one mapping configures a fleet).
- **Payloads**: the whole message as the value, or a `payload_field` (dotted path, e.g.
  `readings.pressure`) to pull one value out of a composite JSON message into its own stream.
- **Security**: anonymous, username/password, and TLS brokers.
- **Control writeback**: a Kelvin control change on a stream is published to that stream's
  `control_topic` and acknowledged with a `ControlChangeStatus`.

## Per-Stream IO Configuration (`io_default.json`)

| Field | Required | Purpose |
|---|---|---|
| `topic` | no | subscribe topic: wildcards + `{asset}`/`{stream}` placeholders |
| `payload_field` | no | dotted path into a JSON payload; omit to use the whole payload |
| `control_topic` | no | topic a control change is written to (writeback); placeholders supported |

A stream needs at least one of `topic` or `control_topic`. Set only `control_topic` for a
**writeback-only** stream: control changes are published to the broker but nothing is ingested.
A stream with neither is ignored and logged as a warning.

See `mqtt-io-example.csv` for example mappings (telemetry field + a controllable setpoint).

## Delivery Semantics

Subscriptions use MQTT **QoS 0**: the broker doesn't redeliver, so any inbound messages published
during a connection gap (broker outage, reconnect) are lost. Control writeback publishes at
**QoS 1** and a control change is only acked `processed` after the broker acknowledges the publish.
Consumers of the Kelvin streams shouldn't assume gapless data.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and passes it through as `app_configuration`.

```yaml
mqtt:
  host: test.mosquitto.org
  port: 1883
  client_id: kelvin-mqtt-importer
  use_tls: false
  auth:
    # omit for an anonymous broker, or:
    username: kelvin-connector
    password: "<broker-password>"
reconnect_interval: 5
```

Run the application: `python3 main.py`. By default it connects to the public `test.mosquitto.org`
broker, so it works once a stream is mapped to a topic.

## Test Locally
Two layers, gated so the default run needs no Docker:

### Unit Tests

```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps (arrow, pandas)
pytest                                           # unit + harness tests (fast, no Docker)
```

- **Unit + harness** (`test_main.py`, `test_settings.py`): pure helpers, control-writeback acks, settings
  validation, and the real `Message` publish path via `KelvinAppTest`.

### Integration Tests

```bash
pip install testcontainers                       # real-broker deps
pytest -m integration                            # smoke tests against a live Mosquitto (Docker required)
```

- **Integration** (`test_integration.py`, marker-gated, deselected by default): boots an
  `eclipse-mosquitto` container and round-trips a message into Kelvin and a control change back to a
  topic, exercising the actual aiomqtt wire path.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: Set the broker host/port on the deployment;
for a secured broker, wire the password as a Secret (`<% secrets.mqtt-password %>`).
