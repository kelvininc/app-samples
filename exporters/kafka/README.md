# Kafka Exporter

This application demonstrates the use of the Kelvin SDK for publishing asset data to Kafka topics: one JSON message per record, produced with an idempotent `acks=all` producer.

It pairs with the [Kafka importer](../../importers/kafka), which consumes records from Kafka into Kelvin; the two share the same `kafka` configuration shape (`bootstrap_servers` + `security`), so one broker's settings are copy-pasteable between them.

## How It Works

1. The app subscribes to Kelvin asset data streams and buffers each message in a local DuckDB store. Only streams with a `topic` in their per-stream IO configuration are buffered; a stream without one is warned at startup and ignored.
2. A background loop drains a batch from the buffer on a fixed interval and produces the records to their mapped topics.
3. The local buffer is trimmed only after the broker acknowledges every record in the batch, so data survives restarts and transient network failures.

A single long-lived producer is reused across batches. If a batch fails, the producer is stopped and recreated on the next upload.

### Topic Mapping

Each stream carries its own destination topic in its IO configuration (set per stream when configuring the exporter's IO on deployment). Topics support `{asset}` and `{stream}` placeholders for fleet templating:

| IO configuration | Asset / stream | Resolved topic |
| --- | --- | --- |
| `telemetry.plant-a` | any | `telemetry.plant-a` |
| `telemetry.{asset}` | `pump-1` / `temperature` | `telemetry.pump-1` |
| `{asset}.{stream}` | `pump-1` / `temperature` | `pump-1.temperature` |

There is no global default topic: every stream that should be exported must define one.

### Message Format

Each buffered row becomes one Kafka message:

- **Value** (JSON): `{"timestamp": "2026-01-01T12:00:00", "asset": "pump-1", "datastream": "temperature", "payload": 42.5}`. The timestamp is ISO-8601; a non-finite float payload (NaN/Inf) is coerced to `null`, since literal NaN/Infinity is invalid JSON.
- **Key**: `asset/datastream` (e.g. `pump-1/temperature`), so one stream's data always lands in the same partition and keeps its ordering.

Topics are not created by the exporter; create them upfront (or enable broker auto-creation). At startup the exporter fetches metadata for every mapped topic, so a missing or forbidden topic fails the deployment visibly.

## Delivery Semantics

Delivery is **at-least-once**: the producer is idempotent with `acks=all` (broker-level retries never duplicate), and the local buffer is trimmed only after every record in the batch is acknowledged. If an acknowledgement is lost (for example a network failure after the broker committed the records), the batch is re-produced on the next upload, producing duplicate messages. Consumers must tolerate or deduplicate duplicates (for example on the key + `timestamp`).

## Security

The `kafka.security` block selects the broker protocol and carries the matching credentials:

| Protocol | Use | Required blocks |
| --- | --- | --- |
| `PLAINTEXT` | Trusted network, no auth | none |
| `SSL` | TLS, optionally mTLS / private CA | optional `tls` |
| `SASL_PLAINTEXT` | SASL auth, no TLS | `sasl` |
| `SASL_SSL` | SASL auth over TLS (managed Kafka, Confluent Cloud, MSK) | `sasl`, optional `tls` |

- `sasl`: `mechanism` (`PLAIN`, `SCRAM-SHA-256`, `SCRAM-SHA-512`), `username`, `password`; all three together, required with a `SASL_*` protocol.
- `tls`: PEM **content**, not paths. `ca_cert` replaces the system trust store (private CA); `client_cert` + `client_key` (both or neither) enable mTLS. Only valid with `SSL`/`SASL_SSL`.

The settings model keeps protocol and blocks coherent: a `SASL_*` protocol without credentials, credentials without a `SASL_*` protocol, or TLS material under a plaintext protocol all fail validation at deploy instead of surfacing as a producer error at runtime.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and passes it through as `app_configuration`.

1. Create `config.yaml` in the app root:
    ```yaml
    kafka:
      bootstrap_servers: "localhost:9092"
      client_id: kelvin-kafka-exporter
      security:
        protocol: PLAINTEXT
    upload:
      interval: 60
      batch_size: 1000
    buffer:
      max_backlog: 0
    ```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** with synthetic data: `kelvin app test simulator`

### Configuration
| Setting | Default | Description |
| --- | --- | --- |
| `kafka.bootstrap_servers` | *(required)* | Comma-separated `host:port` broker list. |
| `kafka.client_id` | `kelvin-kafka-exporter` | Producer client id (broker quotas/monitoring). |
| `kafka.security.protocol` | `PLAINTEXT` | `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, or `SASL_SSL`. |
| `upload.interval` | 60 | Seconds to wait between uploads when the buffer is empty. |
| `upload.batch_size` | 1000 | Maximum number of records drained and produced per upload. |
| `upload.retry.attempts` | 3 | Upload attempts before giving up until the next interval. |
| `upload.retry.base_delay` | 1 | Seconds before the first retry; doubles on each subsequent attempt. |
| `upload.retry.max_delay` | 30 | Ceiling in seconds for the exponential backoff between retries. |
| `buffer.max_backlog` | 0 | Max un-uploaded rows kept before dropping oldest; 0 = unbounded. |

## Test Locally

### Unit Tests
```bash
pip install 'kelvin-python-sdk[testing]'
pytest
```

These cover store/drain/writer logic and settings validation, with the Kafka producer faked (no broker).

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, the same `kafka` / `upload` / `buffer` configuration is set on the deployment rather than in a local `config.yaml`, and each stream's `topic` is set in its IO configuration. Credentials must be **Secrets**; the non-sensitive fields can be set directly.

    1. Create the credential secrets (SASL example):

        ```
        kelvin secret create kafka-password --value "<password>"
        ```

    2. Reference the secrets from the deployment configuration with `<% secrets.<name> %>`:

        ```yaml
        kafka:
          bootstrap_servers: "broker-1:9093,broker-2:9093"
          security:
            protocol: SASL_SSL
            sasl:
              mechanism: SCRAM-SHA-256
              username: kelvin-exporter
              password: "<% secrets.kafka-password %>"
        ```

    > Wire credentials only via `<% secrets... %>`; leave them out of `app.yaml` defaults. A
    > baked-in placeholder is always-truthy and would defeat the "credentials must be set"
    > validation.
