---
name: kelvin-sdk-importer
description: Use when implementing, reviewing, debugging, refactoring, or migrating Kelvin importer applications (`type: importer`) with the Kelvin Python SDK, including importer app.yaml structure, importer_io declarations, io_configuration UI schemas, manual async ingestion loops, runtime-mapped stream configuration, custom action inputs/outputs and `on_custom_action` handling, external connector patterns (MQTT, OPC-UA, REST, etc.), and KRN construction/parsing. Do NOT use for SmartApps (`type: app`); use kelvin-sdk-app instead.
---

# Kelvin SDK — Importer Developer

Build and modify Kelvin importer applications (`type: importer`) with the Kelvin Python SDK. Importers ingest data from external systems (MQTT, OPC-UA, REST, databases, files, etc.) and publish it to the Kelvin Platform. Start with a minimal working implementation, then expand only for explicit requirements.

## Execution Workflow

1. Clarify only missing requirements.
2. Identify connector type (MQTT, OPC-UA, REST, file, database, etc.) and data types.
3. Choose the first reference file with deterministic decision rules.
4. Load only additional references required by the task.
5. Implement with the manual async loop runtime model.
6. Validate against the rule checklist before finalizing.

## Clarify Missing Requirements

Ask at most 2-3 high-impact questions at a time, and only when missing information blocks correct implementation.

Prioritize questions in this order:
- External source: protocol, connection details, and data format.
- Data types: what kinds of streams will be published (number, string, boolean, object).
- Control: whether the importer handles incoming control changes from the platform.
- Custom actions: whether the importer receives custom actions from the platform (e.g. trigger a remote command on the external system) or emits result actions back.
- Importer mapping: per-stream IO configuration shape (what the operator configures per mapped stream).
- Configuration: app-level connector settings (host, port, polling interval, etc.).

If the request is already explicit enough, proceed without extra questions.

When details are missing but non-blocking, proceed with explicit assumptions and mark them clearly:
- Use placeholder names and configuration fields when concrete details are unknown.
- Prefer configurable values (app configuration) over hardcoded constants.
- Do not invent credentials, KRN identifiers, or environment-specific secrets.
- Summarize all assumptions in the final response so they can be confirmed quickly.

## First-File Decision Rules

Pick exactly one first reference file from the list below, then expand only if needed:
- Importer structure, `type: importer`, `importer_io`, `io_configuration`, external ingestion loops, or runtime-mapped streams: [references/importer-apps.md](references/importer-apps.md)
- Kelvin API reads/writes (`app.api`) or timeseries queries: [../kelvin-sdk/references/api-client.md](../kelvin-sdk/references/api-client.md)
- KRN construction/parsing: [../kelvin-sdk/references/krn.md](../kelvin-sdk/references/krn.md)
- Ambiguous runtime failures or mixed-category bugs: [../kelvin-sdk/references/best-practices.md](../kelvin-sdk/references/best-practices.md)

Do not load all references by default. Load only what the current task needs.

## Implementation Defaults

- Use a manual async loop with `await app.connect()` — importers own the event loop.
- Do not use `@app.stream()`, `@app.timer()`, or `@app.task` as the primary ingestion mechanism.
- Build routing from runtime asset/datastream configuration instead of hardcoding asset or stream bindings.
- Read per-stream mapping from `app.assets[asset].datastreams[stream].configuration`.
- Re-read `app.app_configuration` inside reconnect/polling loops so runtime updates take effect.
- Use `importer_io` and `ui_schemas.io_configuration` — never use `data_streams.inputs` for external signals.
- Catch both connector-library exceptions and `OSError` for network failures in reconnect loops.
- For custom actions, declare `custom_actions.inputs` / `custom_actions.outputs` at the root of `app.yaml` (the same schema SmartApps use, not a field of `importer_io`) and wire `app.on_custom_action` before `await app.connect()`. Publish `CustomActionResult` back to acknowledge.

## Validation Checklist

### `app.yaml` and schema alignment

- Use `type: importer`, not `type: app`.
- Use `importer_io` to declare ingest capabilities, not `data_streams.inputs`.
- Keep `ui_schemas.io_configuration.<profile>` aligned with `importer_io[].name`.
- Use `defaults.configuration` for connector settings (host, port, polling interval, etc.).
- If the importer handles custom actions, declare them under a top-level `custom_actions:` block (with `inputs:` and/or `outputs:` listing `type:` entries) — this is the same schema as SmartApps and lives outside `importer_io`.
- Do not declare Kelvin runtime credentials (`KELVIN_CLIENT__URL`, `KELVIN_CLIENT__CLIENT_ID`, `KELVIN_CLIENT__CLIENT_SECRET`) in `defaults.system.environment_vars`; the platform injects them.
- Follow naming conventions: app names with hyphens, stream/parameter names with underscores.

### Runtime and data publishing

- Call `await app.connect()` explicitly; do not use `app.run()`.
- Read per-stream mapping from `asset_info.datastreams[stream_name].configuration`.
- Publish with `Message` and `KMessageTypeData`, matching the `primitive` to the mapped stream data type.
- Publish only to mapped asset/stream targets derived from runtime IO configuration.
- If `custom_actions.inputs` is declared, assign `app.on_custom_action = <async handler>` before `await app.connect()` and respond with `CustomActionResult` (or publish to a declared `custom_actions.outputs` type).

### Reliability and safety

- Catch connector-library exceptions and `OSError` in reconnect loops.
- Log connection state, reconnection attempts, and mapping issues with context.
- Keep secrets and credentials out of source files.
- Do not add broker usernames or passwords unless the external system actually requires them.

## Framework Guarantees

Assume these SDK guarantees and avoid redundant checks:
- `app.assets` contains the current set of deployed assets with their datastream configurations.
- `asset_info.datastreams[stream].configuration` contains the operator-configured per-stream mapping.
- `app.app_configuration` contains the current global app configuration.

Validate connector-specific logic (connection parameters, data parsing, protocol handling), not framework invariants.
