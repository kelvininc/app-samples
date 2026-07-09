# Best Practices and Common Pitfalls

## When to Use

Use this file as a final implementation/review checklist and to debug common Kelvin app failures. Applies to both SmartApps (`type: app`) and importers (`type: importer`).

## Table of Contents
- [When to Use](#when-to-use)
- [Pre-Flight Checklist](#pre-flight-checklist)
- [App.yaml and UI Schemas](#appyaml-and-ui-schemas)
- [Streams and Windows (SmartApps)](#streams-and-windows-smartapps)
- [Messages, Recommendations, and Actions (SmartApps)](#messages-recommendations-and-actions-smartapps)
- [Importer Runtime and Data Publishing](#importer-runtime-and-data-publishing)
- [API and Timeseries](#api-and-timeseries)
- [State and Concurrency](#state-and-concurrency)
- [Error Handling](#error-handling)
- [Logging and Security](#logging-and-security)

## Pre-Flight Checklist

- Confirm every published output is declared in `app.yaml` before publishing.
- Confirm parameter names match exactly between `app.yaml` and `ui_schemas/*`.
- Confirm stream names used in code match declarations and naming rules.
- For SmartApps, confirm long-running logic runs in `@app.task` or timers (not blocking stream handlers).
- For SmartApps, confirm recommendations/actions include explicit expiration/timeout behavior.
- For importers, confirm `importer_io` and `ui_schemas.io_configuration` are aligned and runtime mapping is read from deployed asset/datastream configuration.

## App.yaml and UI Schemas

- Do not create a `configuration:` declaration section in `app.yaml`.
- Put global values under `defaults.configuration` and per-asset values under `parameters`.
- Use app names with hyphens; use snake_case for stream/parameter names (dots only when needed).
- Keep UI schemas aligned with declared parameter/configuration keys.
- For importers, do not declare external topics or signals as `data_streams.inputs`; use `importer_io` and `io_configuration` instead.
- For importers, do not manually declare Kelvin client credentials in `defaults.system.environment_vars`.

## Streams and Windows (SmartApps)

- Use decorator-based handlers (`@app.stream`, `@app.timer`, `@app.task`) unless explicitly asked for function-based patterns.
- For windows, always set `inputs=[...]` explicitly.
- Read DataFrame columns by stream names, not generic `payload`/`value` keys.
- Always guard with `df.empty` and NaN checks before numeric calculations.

## Messages, Recommendations, and Actions (SmartApps)

- Use the correct message class for each output type.
- Embed control changes and custom actions inside `Recommendation` unless explicitly asked to publish standalone.
- Add `kelvin_closed_loop` when recommendations include control changes or custom actions.
- Send `ControlAck` only when handling incoming control changes (`control_changes.inputs`).

## Importer Runtime and Data Publishing

- Call `await app.connect()` explicitly; do not use `app.run()`.
- Own the async ingestion/reconnect loop manually.
- Read per-stream mapping from `asset_info.datastreams[stream_name].configuration`.
- Re-read `app.app_configuration` inside reconnect/polling loops when runtime updates are enabled.
- Publish with `Message` and `KMessageTypeData`, matching the `primitive` to the mapped stream data type.
- Publish only to mapped asset/stream targets derived from runtime IO configuration.
- Do not use `@app.stream()`, `@app.timer()`, or `@app.task` as the primary ingestion mechanism.

## API and Timeseries

- Use real-time streams/windows for recent data and Timeseries API for historical queries.
- Do not declare streams in `data_streams.inputs` when they are used only through Timeseries API.

## State and Concurrency

- Protect shared mutable state in concurrent handlers (`asyncio.Lock` when needed).
- Handle asset add/remove events (`on_asset_change`) to keep per-asset state consistent.
- Clean up removed asset state to prevent stale memory growth.

## Error Handling

- For SmartApps: `@app.stream` and `@app.timer` continue after exceptions; `@app.task` does not.
- Wrap long-running task loops in `try/except` and log exceptions with context.
- Validate business/domain rules only; rely on framework guarantees for typed payload/resource fields.
- For importers, catch network-layer failures such as `OSError` in reconnect loops in addition to connector-library exceptions.

## Logging and Security

- Log asset, stream, and decision context for operational debugging.
- Keep secrets out of source; load them via `defaults.system.environment_vars`.
- Do not add broker usernames or passwords unless the external system actually requires them.
- Avoid logging secret values or full sensitive payloads.
