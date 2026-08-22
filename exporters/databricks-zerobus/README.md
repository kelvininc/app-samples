# Databricks Zerobus Exporter

This application demonstrates the use of the Kelvin SDK for streaming data directly into a Databricks Unity Catalog Delta table using the [Zerobus Ingest API](https://docs.databricks.com/aws/en/ingestion/zerobus-overview).

Zerobus is a push-based ingestion API that writes records straight into a Delta table over a gRPC stream. Unlike the [Databricks Volume Exporter](../databricks-volume), there are no parquet files, no Unity Catalog volume, and no Auto Loader / COPY INTO ingestion job; the records land in the table directly.

## How It Works

1. The app subscribes to Kelvin asset data streams and buffers each message in a local DuckDB store.
2. A background loop drains a batch from the buffer on a fixed interval and ingests the records over a Zerobus stream.
3. The local buffer is trimmed only after the Zerobus server acknowledges the batch, so data survives restarts and transient network failures.

A single long-lived stream is reused across batches. If a batch fails, the stream is dropped and recreated on the next upload. Each record is built as a JSON-serializable dict (`timestamp`, `asset`, `datastream`, `payload`); the timestamp is serialized to ISO-8601 and a non-finite float payload (NaN/Inf) is coerced to `null`, since the record is serialized to JSON for ingestion.

The local DuckDB buffer is deliberate: Zerobus only guarantees durability *after* a record is acknowledged, and its client-side recovery is an in-memory retry window of a few tens of seconds. The on-disk buffer is what protects data across longer outages and process restarts, so it is kept even though Zerobus is a streaming API.

## Delivery Semantics
Delivery is **at-least-once**: the local buffer is trimmed only after the Zerobus server acknowledges the batch. If an acknowledgement is lost (for example a network failure after the server committed the records), the batch is re-ingested on the next upload, producing duplicate rows. Consumers must tolerate or deduplicate duplicates (for example on `timestamp` + `asset` + `datastream`).

Within a single buffered batch, writes are **last-write-wins** on `timestamp` + `asset` + `datastream`: if a corrected value arrives for a key that's still buffered, it replaces the earlier value (upsert), so only the latest value for that key is ingested. Once a value has been ingested and trimmed, a later correction for the same key is sent as a new record and lands as a duplicate row downstream.

## Databricks Setup

### 1. Create the Delta Table

Zerobus does not create tables; create the target table yourself with a schema that matches the exported records:

```sql
CREATE TABLE IF NOT EXISTS <catalog>.<schema>.<table> (
    timestamp TIMESTAMP_NTZ,
    asset STRING,
    datastream STRING,
    payload VARIANT
)
USING DELTA;
```

> `payload` is a `VARIANT` so the table holds number, string, and boolean stream values with
> their types preserved. Each Zerobus record carries the native value, which lands in the
> variant column. Requires Databricks Runtime 15.4 LTS or later (or Serverless SQL). On older
> runtimes use `payload STRING` instead; the exporter writes the same scalar value either way.
>
> Query typed values back with `payload::double`, `payload::string`, `payload::boolean`, and
> inspect the stored type with `schema_of_variant(payload)`.

### 2. Create a Service Principal

In your Databricks workspace, go to **Settings > Identity and Access > Service principals**, add a new service principal, then generate an OAuth secret (client ID and client secret). Note the Application Id (UUID) from the Configurations tab. Zerobus authenticates only with an OAuth service principal; there is no personal-access-token path.

### 3. Grant Permissions

Grant the service principal access to the catalog, schema, and target table:

```sql
GRANT USE CATALOG ON CATALOG <catalog> TO `<application-id>`;
GRANT USE SCHEMA ON SCHEMA <catalog>.<schema> TO `<application-id>`;
GRANT MODIFY, SELECT ON TABLE <catalog>.<schema>.<table> TO `<application-id>`;
```

### 4. Find Your Endpoints

The app needs two URLs:

- **Workspace hostname** (`server_hostname`): your workspace hostname, used for OAuth. Example: `dbc-xxxxxxxx-xxxx.cloud.databricks.com`
- **Zerobus endpoint** (`zerobus_endpoint`): the gRPC ingestion hostname, in the form `<workspace-id>.zerobus.<region>.cloud.databricks.com`. The `<workspace-id>` is the number after `o=` in your browser URL, and `<region>` is your workspace region. Example: `1234567890123456.zerobus.eu-west-1.cloud.databricks.com`

Both are bare hostnames (no `https://`), matching the other Databricks exporters; the app adds the scheme. An explicit `https://` is also accepted.

> If the endpoint fails to connect, confirm that Zerobus Ingest is enabled for your workspace and region, and that any egress firewall allows the regional Zerobus IPs.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform injects on deployment. For local runs, put a `config.yaml` in the app root (next to `main.py`); the SDK reads it and passes it through as `app_configuration`.
There are no `DATABRICKS_*` environment variables; everything lives under the
`databricks`, `upload`, and `buffer` blocks.

1. Create `config.yaml` in the app root:
    ```yaml
    databricks:
      server_hostname: "dbc-xxxxxxxx-xxxx.cloud.databricks.com"
      zerobus_endpoint: "1234567890123456.zerobus.eu-west-1.cloud.databricks.com"
      delta_table: "<catalog>.<schema>.<table>"
      auth:
        # OAuth machine-to-machine (M2M) service principal:
        client_id: "<client-id>"
        client_secret: "<client-secret>"
    upload:
      interval: 60
      batch_size: 1000
    ```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** with synthetic data: `kelvin app test simulator`

### Configuration
| Setting | Default | Description |
| --- | --- | --- |
| `upload.interval` | 60 | Seconds to wait between uploads when the buffer is empty. |
| `upload.batch_size` | 1000 | Maximum number of records drained and ingested per upload. |
| `upload.retry.attempts` | 3 | Upload attempts before giving up until the next interval. |
| `upload.retry.base_delay` | 1 | Seconds before the first retry; doubles on each subsequent attempt. |
| `upload.retry.max_delay` | 30 | Ceiling in seconds for the exponential backoff between retries. |
| `buffer.max_backlog` | 1000000 | Max un-uploaded rows kept before dropping oldest; set 0 for unbounded. |

## Test Locally

### Unit Tests
```bash
pip install 'kelvin-python-sdk[testing]'
pytest
```

These cover store/drain/writer logic and settings validation, with the Zerobus client faked (no network).

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, the same `databricks` / `upload` / `buffer` configuration is set on the
deployment rather than in a local `config.yaml`. Credentials must be **Secrets**; the
non-sensitive fields (`server_hostname`, `zerobus_endpoint`, `delta_table`) can be set
directly in the deployment configuration or wired to secrets too, whichever you prefer.

    1. Create the credential secrets:

        ```
        kelvin secret create databricks-client-id --value "<client-id>"
        kelvin secret create databricks-client-secret --value "<client-secret>"
        ```

    2. Reference the secrets from the deployment configuration with `<% secrets.<name> %>`,
       and set the non-sensitive fields directly:

        ```yaml
        databricks:
          server_hostname: "dbc-xxxxxxxx-xxxx.cloud.databricks.com"
          zerobus_endpoint: "1234567890123456.zerobus.eu-west-1.cloud.databricks.com"
          delta_table: "<catalog>.<schema>.<table>"
          auth:
            client_id: "<% secrets.databricks-client-id %>"
            client_secret: "<% secrets.databricks-client-secret %>"
        ```

    > Wire credentials only via `<% secrets... %>`; leave them out of `app.yaml` defaults. A
    > baked-in placeholder is always-truthy and would defeat the "credentials must be set"
    > validation.
