# Snowflake Exporter
This application demonstrates the use of the Kelvin SDK for uploading streaming data to a Snowflake table.

Incoming streaming data is buffered in a local DuckDB file, then drained in batches and written with a
single parameterized multi-row `INSERT ... SELECT ..., PARSE_JSON(column4) FROM VALUES (?, ?, ?, ?), ...`
statement. Values are bound as plain parameters and `PARSE_JSON` wraps the payload column on the SELECT
side. Number, string, and boolean values keep their native types through a `VARIANT` payload column.

## Delivery Semantics
Delivery is **at-least-once**: the buffer is trimmed only after the multi-row `INSERT` commits. If the app crashes after the insert commits but before the buffer trim, the same batch is inserted again on restart, producing duplicate rows. Consumers must tolerate or deduplicate duplicates (for example on `timestamp` + `asset` + `datastream`).

Within the buffer, records sharing the same (`timestamp`, `asset`, `datastream`) are deduplicated last-write-wins before export, so a corrected value for an already-buffered key replaces the earlier one rather than exporting both.

## Snowflake Setup

Create the target table (the payload is a `VARIANT` so it holds number/string/boolean values with their
types preserved):

```sql
CREATE TABLE IF NOT EXISTS <database>.<schema>.<table> (
    timestamp TIMESTAMP_NTZ,
    asset STRING,
    datastream STRING,
    payload VARIANT
);
```

Query typed values back with `payload::double`, `payload::string`, `payload::boolean`.

The configured user (or its role) needs `USAGE` on the warehouse, database, and schema, plus `SELECT`
and `INSERT` on the table; the startup probe runs `SELECT ... LIMIT 0` against the table to fail fast
on a missing object or grant, and the drain loop inserts into it.

### Authentication

Two methods are supported via `snowflake.auth.method`:

- **`password`**: set `auth.password`.
- **`key_pair`** (recommended for service accounts): set `auth.private_key` to the PEM-encoded RSA
  private key (and `auth.private_key_passphrase` if it's encrypted). Register the matching public key on
  the Snowflake user with `ALTER USER <user> SET RSA_PUBLIC_KEY='...'`.

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
    snowflake:
      account: "<account>"        # e.g. xy12345.us-east-1
      user: "<user>"
      warehouse: "<warehouse>"
      database: "<database>"
      schema: "<schema>"
      table: "<table>"
      auth:
        method: password          # or "key_pair"
        password: "<password>"
        # method: key_pair
        # private_key: "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
        # private_key_passphrase: "<passphrase>"
    upload:
      interval: 60
      batch_size: 1000
    ```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** with synthetic data: `kelvin app test simulator`

## Test Locally

### Unit Tests
The test suite needs the SDK's testing extra on top of `requirements.txt`; pytest collection
imports `kelvin.testing`, which a plain `pip install -r requirements.txt` doesn't provide:

```sh
pip3 install "kelvin-python-sdk[testing]" pytest pytest-asyncio
python3 -m pytest
```

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: On a cluster, the same `snowflake` / `upload` / `buffer` configuration is set on the deployment. The
credentials must be **Secrets**; the non-sensitive fields can be set directly.

```
kelvin secret create snowflake-password --value "<password>"
# or, for key-pair auth:
kelvin secret create snowflake-private-key --value "<pem-private-key>"
```

Reference the secret(s) from the deployment configuration with `<% secrets.<name> %>`:

```yaml
snowflake:
  account: "<account>"
  user: "<user>"
  warehouse: "<warehouse>"
  database: "<database>"
  schema: "<schema>"
  table: "<table>"
  auth:
    method: password
    password: "<% secrets.snowflake-password %>"
```

> Wire credentials only via `<% secrets... %>`; leave them out of `app.yaml` defaults so a baked-in
> placeholder can't be mistaken for a real credential (it would defeat the one-auth validation).
