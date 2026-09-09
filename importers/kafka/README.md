# Kafka Importer
This application demonstrates the use of the Kelvin SDK for importing data from Apache Kafka.

A bidirectional Kafka connector. It consumes records from the topics mapped to each Kelvin
datastream, publishes the values into Kelvin, and produces Kelvin control changes back to Kafka.

An operator maps each Kelvin asset/stream to a Kafka topic in the Kelvin UI (the `io_configuration`
schema). The connector reads the mapping from `app.assets[...].datastreams[...].configuration`.
Configuration or stream-mapping changes require **redeploying the workload**; the platform restarts
it on config change, and the connector picks up the new mapping at startup. The stream's
**declared data type** drives how record values are decoded; there is no separate `primitive` to
keep in sync.

## Capabilities

- **Topics**: a Kafka topic per stream, with `{asset}`/`{stream}` placeholders that expand per
  deployed stream (one mapping configures a fleet).
- **Record key filter**: optional `key`: only consume records whose key matches (one topic keyed by entity). Supports placeholders.
- **Payloads**: the whole record value, or a `payload_field` (dotted path) to pull one value out of
  a composite JSON record into its own stream. JSON values (Avro/Protobuf + Schema Registry is out
  of scope here).
- **Security**: PLAINTEXT / SSL / SASL (PLAIN, SCRAM-SHA-256/512), with optional TLS material
  (`kafka.security.tls`): a private CA bundle and/or an mTLS client cert/key pair, as PEM content
  wired to secrets. The `kafka.security` block is shared with the
  [Kafka exporter](../../exporters/kafka), so one broker's settings are copy-pasteable between them.
- **Control writeback**: a Kelvin control change is produced to the stream's `control_topic`
  (keyed by asset) and acknowledged with a `ControlChangeStatus`.

## Per-Stream IO Configuration (`io_default.json`)

| Field | Required | Purpose |
|---|---|---|
| `topic` | no | Kafka topic to consume; `{asset}`/`{stream}` placeholders |
| `payload_field` | no | dotted path into a JSON record value; omit for the whole value |
| `key` | no | only consume records with this key; placeholders supported |
| `control_topic` | no | topic a control change is produced to (writeback) |

A stream needs at least one of `topic` or `control_topic`. Set only `control_topic` for a
**writeback-only** stream: control changes are produced to Kafka but nothing is ingested.
A stream with neither is ignored and logged as a warning.

## Delivery Semantics

The consumer relies on Kafka **auto-commit** (5 s interval). Two edge cases follow:

- **At-most-once on crash**: an offset can be committed after a record is handed to the connector
  but before it is published to Kelvin; if the connector crashes in that window, the record is lost.
- **Duplicates on reconnect**: after a reconnect, everything since the last committed offset is
  re-delivered and published again.

Consumers of the Kelvin streams should tolerate both occasional gaps and duplicate values.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and passes it through as `app_configuration`.

```yaml
kafka:
  bootstrap_servers: localhost:9092
  group_id: kelvin-kafka-importer
  auto_offset_reset: latest
  security:
    protocol: SASL_SSL
    sasl:
      mechanism: SCRAM-SHA-256
      username: kelvin-connector
      password: "<broker-password>"
    # For mTLS or a private CA (SSL / SASL_SSL only), add PEM content:
    # tls:
    #   ca_cert: "<ca-pem>"           # optional; empty = system CAs
    #   client_cert: "<cert-pem>"     # both or neither
    #   client_key: "<key-pem>"
reconnect_interval: 5
```

> **2.0.0 breaking change**: `security_protocol` and `sasl` moved under `kafka.security`
> (`security.protocol` / `security.sasl`), and the optional `security.tls` block was added.
> Migrate deployment configurations when upgrading from 1.x.

Run the application: `python3 main.py`.

## Test Locally

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
pytest -m integration                            # smoke tests against a live Kafka (Docker required)
```

- **Integration** (`test_integration.py`, marker-gated, deselected by default): boots a
  `confluentinc/cp-kafka` container and round-trips a record into Kelvin and a control change back to a
  topic, exercising the actual aiokafka wire path.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: Set the brokers/group on the deployment; for SASL, wire the password as a Secret (`<% secrets.kafka-password %>`).
