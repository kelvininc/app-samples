# Kelvin SDK Sample App Style Guide

This guide documents how to structure and write sample applications for this repository.

For SDK reference and API documentation, see the [Kelvin Documentation](https://docs.kelvin.ai).

Terminology used throughout: **Kelvin** is the platform apps build against and stream data
into; **Kelvin Cloud** is a user's Kelvin instance, the environment you deploy to.

---

## Table of Contents

1. [Sample Types](#1-sample-types)
2. [Shared Conventions](#2-shared-conventions)
3. [README Template](#3-readmemd-template)
4. [Per-Archetype Guide](#4-per-archetype-guide)
5. [Checklist Before Submission](#5-checklist-before-submission)

---

## 1. Sample Types

Every sample is one of five archetypes. Find yours first; the shared conventions in section 2
apply to all of them, and section 4 covers what each one does differently.

| Archetype | `type:` | Folder | Use it when |
|-----------|---------|--------|-------------|
| **SmartApp** | `app` | `applications/` | You process asset streams and emit control changes or recommendations. |
| **Importer** | `importer` | `importers/` | You pull data from an external system into Kelvin (optionally writing control changes back). |
| **Exporter (storage)** | `exporter` | `exporters/` | You buffer asset data and upload it to a store (S3, a table, a volume). |
| **Exporter (action)** | `exporter` | `exporters/` | You handle a custom action and call an external service (email, Slack, a webhook). |
| **Docker** | `docker` | `docker/` | You ship a plain container (a broker, a proxy), not a Python SDK app. |

---

## 2. Shared Conventions

These apply to every archetype.

### Folder structure

Every sample ships this core:

```
app-name/
├── README.md            # Documentation (required)
├── app.yaml             # Kelvin manifest (required)
├── Dockerfile           # Container build (required)
├── requirements.txt     # Runtime dependencies (required)
├── .dockerignore        # Build exclusions (required)
├── pyrightconfig.json   # Type-checker config
└── pytest.ini           # Test config
```

Python SDK archetypes (everything except Docker) add `main.py`, `settings.py`, a `tests/`
folder, and `ui_schemas/`. Section 4 lists the extra files each archetype needs.

### Naming conventions

| Element | Convention | Example |
|---------|------------|---------|
| App folder | lowercase-with-hyphens | `event-detection` |
| App name in `app.yaml` | lowercase-with-hyphens | `event-detection` |
| App title in `app.yaml` | Descriptive name + archetype suffix (see below) | `Kafka Exporter` |
| Data streams | lowercase_with_underscores | `motor_temperature` |
| Parameters / config keys | lowercase_with_underscores | `max_temperature` |
| Python files | lowercase_with_underscores | `main.py`, `writer.py` |

Validation regex: `^[a-z0-9]([-_.a-z0-9]*[a-z0-9])?$`

The `title` is what users see in the app catalog, so it must say what the app *is* on its
own: a Kafka exporter and a Kafka importer both titled "Kafka" are indistinguishable in a
list. Suffix by archetype: ` Exporter` (storage and action), ` Importer`, ` Broker`/` Server`
for Docker infra images, ` Machine Simulator` for simulators. SmartApps use their function
name with no suffix (`Event Detection`). The README's top heading matches the title.

### Level guidelines

Set `Level` in the README to match the sample's complexity.

| Level | Criteria |
|-------|----------|
| **Beginner** | Single pattern, under 50 lines of logic, no dependencies beyond the SDK. |
| **Intermediate** | Several SDK features, 50-150 lines, external libraries allowed. |
| **Advanced** | Complex patterns (ML, optimization), over 150 lines, multiple integrations. |

### Logging and errors

- Log with `kelvin.logs.logger` (structured key-value pairs), never `print`.
- Handle exceptions inside `@app.task` bodies; the SDK does not auto-catch them the way it does for stream and timer handlers.
- Prefer an explicit `raise RuntimeError(...)` over `assert` for runtime guards, since `python -O` strips asserts.

Write log messages for the operator reading them at 3am, not for the code:

- **One INFO line per unit of work.** Exporters log one line per delivered batch, importers
  one summary per interval (`Ingested from Kafka  rows=8123 topics=4`); never one line per
  message. Carry the numbers that show health: `rows=`, and `backlog=` (rows still buffered)
  so falling-behind is visible from any single line.
- **Reserve loss words for loss.** "discarded" means data is gone (cap eviction, unmapped
  rows); post-ack cleanup is "Cleared uploaded rows from buffer", never "dropped".
- **Always pair `error=str(e)` with `error_type=type(e).__name__`.** `str(e)` is empty for
  `TimeoutError`, `ConnectionError`, and some `SSLError`s; a log line reading `error=` is
  useless.
- **Make incidents bounded.** Repeated failures carry a `consecutive_failures=` counter, and
  the first success after failures logs a recovery marker (`Upload recovered  failed_ticks=`).
  Per-record warnings that can flood (unparseable input) log full detail once per interval
  and are otherwise counted into the summary.
- **Report carried-over state at startup, not shutdown.** `Buffer ready  pending_rows=N`;
  shutdown logs are the ones lost to crashes and OOM kills.
- **Never log raw config values.** Validation failures use
  `e.errors(include_url=False, include_input=False)`; the offending input may be a credential.

### Configuration and secrets

- Build settings from `app.app_configuration` with a pydantic `BaseSettings` model, and set `model_config = SettingsConfigDict(extra="ignore")` so platform-injected keys never crash a valid deployment.
- For local runs, read a `config.yaml` next to `main.py`; the SDK passes it through as `app_configuration`.
- Never hardcode credentials. Type them as `SecretStr`, wire them on the deployment as `<% secrets.<name> %>`, and normalize an unresolved `<% secrets... %>` literal to unset in a `mode="before"` validator so a forgotten secret fails config validation instead of leaking the placeholder downstream.

### UI schemas and upload validation

`kelvin app upload` validates `defaults.configuration` against the `ui_schemas` JSON schemas,
and the defaults are intentionally incomplete scaffolding (empty strings for
required-to-deploy fields, no credentials since those wire to secrets). A schema that is
strict about content therefore breaks the upload. The rules:

- **Content constraints must tolerate empty.** Use `"pattern": "^$|^[a-z0-9]{3,24}$"` instead
  of `minLength`/a bare pattern on any field whose default is `""`. The real "must be set"
  gate is the pydantic settings model, which fails the deployment at connect.
- **No `required` on credential fields** inside method-conditional `if/then` blocks; the
  default `method` value triggers the branch and the credentials are absent by design.
- **Never a bare `auth:` key in yaml defaults**; it parses as `null` and fails
  `"type": "object"`. Use `auth: {}` or omit the key.
- **Dropdown values must be strings.** The platform's form validates select values as
  strings, so integer `oneOf`/`const` options fail with "must be a string"; use string consts
  (`"1"`, `"2"`) with `title` labels and coerce in the app.

### Dependencies

- `requirements.txt` is runtime-only; it ships to the container. Keep `pytest`, `testcontainers`, and other test tools out of it.
- Always include `kelvin-python-sdk`. Add the `[ai]` extra only when the app uses pandas or numpy, directly or through the SDK's DataFrame/window features.
- List every external library the app imports.
- All samples target **Python 3.13**.

---

## 3. README.md Template

Every README follows the same spine, so a reader who knows one knows them all. The order
`Prerequisites → Run Locally → Test Locally → Kelvin Cloud Deployment` is identical
everywhere; the *(optional)* sections slot in at fixed positions when they apply. Section
headings use Title Case.

~~~markdown
# {App Title}

{One or two paragraphs: what this sample does and how data flows through it. Open with
"This application demonstrates the use of the Kelvin SDK for {…}."}

## Architecture Diagram
<!-- Optional. Only when the folder ships an assets/architecture-diagram image. -->
![Architecture](./assets/architecture-diagram.jpg)

## {Overview Section}
<!-- Optional, archetype-specific, pinned right after the description/Architecture:
     - SmartApp:            "## How It Works"
     - Storage exporter:    "## Delivery Semantics"
     - MQTT / Kafka:        "## Capabilities" + "## Per-Stream IO Configuration"
     - Action exporter:     "## Failure Behavior" -->

## {Service} Setup
<!-- Optional. Service-side setup done OUTSIDE Kelvin before this runs: create the
     Snowflake/Delta table and grants, register the Slack app, create the Teams webhook.
     Name it after the service. Omit when there is nothing to set up. -->

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Configuration is read from `app.app_configuration`, the same nested structure the platform
injects on deployment. For local runs, put a `config.yaml` in the app root (next to
`main.py`); the SDK reads it and passes it through as `app_configuration`.

1. Create `config.yaml` in the app root:
    ```yaml
    {config block for this sample}
    ```
2. **Run** the application: `python3 main.py`
3. Open a new terminal and exercise it (`kelvin app test simulator`, replay a CSV, publish a custom action, etc.).

## Test Locally
### Unit Tests
```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps
pytest                                           # fast, no Docker
```
{What the default run covers.}

### Integration Tests
<!-- Optional. Only when an integration test exists (or to explain why none does). -->
```bash
pip install testcontainers                       # real-server deps
pytest -m integration                            # smoke test against a live server (Docker required)
```
{What the container test covers. Marker-gated, deselected by default.}

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: set the same config on the deployment instead of a local `config.yaml`, and
   wire every credential as a **Secret**, referenced with `<% secrets.<name> %>`.
    ```
    kelvin secret create {name} --value "<value>"
    ```
    ```yaml
    {deployment config block referencing <% secrets.<name> %>}
    ```

> Wire credentials only via `<% secrets... %>`; leave them out of `app.yaml` defaults so a
> baked-in placeholder can't be mistaken for a real credential.
~~~

---

## 4. Per-Archetype Guide

Each archetype shares section 2 but differs in its extra files, its `app.yaml` IO block, its
`main.py` shape, its config surface, and how it's tested.

### 4.1 SmartApp (`type: app`)

Processes asset data streams and publishes control changes or recommendations.

- **Adds:** `ui_schemas/parameters.json`, plus sample data (a `csv/` folder, or `model/` and `assets/` for ML apps).
- **`app.yaml`:** `category: smartapp`, a `data_streams` block, a `control_changes` block when it acts back on the asset, and `parameters` with `defaults.parameters`.

    ```yaml
    type: app
    category: smartapp
    data_streams:
      inputs:
        - name: motor_temperature
          data_type: number
    control_changes:
      outputs:
        - name: motor_speed_set_point
          data_type: number
    parameters:
      - name: temperature_max_threshold
        data_type: number
    ```

- **`main.py`:** decorator handlers on a `KelvinApp()`. Read per-asset knobs from `app.assets[asset].parameters`; publish `ControlChange` / `Recommendation`.

    ```python
    @app.stream(inputs=["motor_temperature"])
    async def on_temperature(msg: AssetDataMessage) -> None:
        ...
    ```

- **Config surface:** asset-level `parameters` (per-asset knobs), set in `ui_schemas/parameters.json`.
- **Testing:** replay sample streams with `kelvin app test csv --csv csv/<file>.csv`; add unit tests for the logic (thresholds, window math).

### 4.2 Importer (`type: importer`)

Pulls data from an external system into Kelvin, optionally writing control changes back.

- **Adds:** `settings.py`, `ui_schemas/configuration.json`, `ui_schemas/io_default.json`, `tests/`.
- **`app.yaml`:** an `importer_io` block (the data types it publishes, and `control: true` for writeback) and an `io_configuration` UI schema for the runtime stream mapping.

    ```yaml
    type: importer
    importer_io:
      - name: default
        data_types: [number, string, boolean]
        control: true
    ui_schemas:
      configuration: "ui_schemas/configuration.json"
      io_configuration:
        default: "ui_schemas/io_default.json"
    ```

- **`main.py`:** build `Settings(**app.app_configuration)`, connect to the source, run an ingestion loop that publishes into Kelvin, and reconnect on failure. Use `@app.on_control_change` for writeback.
- **Config surface:** app-level `configuration` (connection settings) plus per-stream `io_configuration` an operator maps at runtime (topic, payload field, etc.).
- **Testing:** unit and harness tests (`KelvinAppTest`); an optional integration test that boots the real broker via testcontainers.

### 4.3 Exporter, storage (`type: exporter`)

Buffers asset data and uploads it to an external store.

- **Adds:** `settings.py`, `store.py` (DuckDB buffer), `drain.py` (batch drain loop), `writer.py` (the sink), `ui_schemas/configuration.json`, `tests/`.
- **`app.yaml`:** an `exporter_io` block, a `configuration` block with the provider, `upload`, and `buffer` settings, and a persistent volume for the buffer.

    ```yaml
    type: exporter
    exporter_io:
      - name: default
        data_types: [number, string, boolean]
    defaults:
      system:
        volumes:
          - name: data
            target: data.db
            type: persistent
    ```

- **`main.py`:** `@app.stream` buffers each message into the store, `@app.task` drains batches through the writer, and `@app.on_connect` / `@app.on_disconnect` own the writer and buffer lifecycle.
- **Config surface:** app-level `configuration` (provider credentials, `upload`, `buffer`).
- **Testing:** unit tests for store/drain/writer/settings with the client faked; an optional integration test against a real server (MinIO, an SFTP container).

### 4.4 Exporter, action (`type: exporter`)

Handles a custom action and calls an external service. No buffer, no volume.

- **Adds:** `settings.py`, `<service>_integration.py`, `ui_schemas/configuration.json`, `tests/`.
- **`app.yaml`:** a `custom_actions` block declaring the action type it listens for.

    ```yaml
    type: exporter
    custom_actions:
      inputs:
        - type: Slack Message
    ```

- **`main.py`:** `@app.on_connect` builds the integration once, `@app.on_custom_action` validates the payload, calls the service, and acks with a `CustomActionResult` (`success: true/false` and a reason).
- **Config surface:** app-level `configuration` (service credentials).
- **Testing:** unit tests for the integration against a faked client, plus a harness test of the action-to-result flow.

### 4.5 Docker (`type: docker`)

A plain container, not a Python SDK app.

- **Ships:** `Dockerfile`, `entrypoint.sh`, `app.yaml`, `README.md`, `ui_schemas/configuration.json`. No `main.py`, no `settings.py`, no `tests/`.
- **`app.yaml`:** `type: docker` with a `system` block for volumes, ports, and `environment_vars`. Configure the container through `environment_vars`, wiring credentials to `<% secrets... %>`.
- **Config surface:** `environment_vars` on the deployment.
- **Testing:** manual; run the container and exercise it.

---

## 5. Checklist Before Submission

Shared:

- [ ] Required files present (`README.md`, `app.yaml`, `Dockerfile`, `requirements.txt`, `.dockerignore`).
- [ ] `app.yaml` sets `spec_version: 5.0.0` and the correct `type`.
- [ ] README follows the section-3 spine.
- [ ] No hardcoded credentials; secrets wired via `<% secrets... %>`.
- [ ] Logging uses `kelvin.logs.logger`, not `print`.
- [ ] Level reflects complexity, and naming conventions are followed.

Python SDK archetypes (SmartApp, Importer, Exporter) also:

- [ ] `ui_schemas/` present (`parameters.json` for SmartApps; `configuration.json`, plus `io_default.json` for importers).
- [ ] App runs locally with `python3 main.py`.
- [ ] Test dependencies stay out of `requirements.txt`.
- [ ] Unit tests pass with `pytest`; integration tests, if any, are marker-gated behind `-m integration`.

---

## Questions?

For SDK reference and detailed API documentation, see the [Kelvin Documentation](https://docs.kelvin.ai).

For issues or questions about this repository, open an issue on GitHub.
