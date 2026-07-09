---
name: kelvin-sdk-app
description: Use when implementing, reviewing, debugging, refactoring, or migrating Kelvin SmartApps (`type: app`) with the Kelvin Python SDK, including app.yaml schema/configuration, stream and window handlers, recommendations/control changes/custom actions, data quality, Kelvin API client usage, and KRN construction/parsing. Do NOT use for importer applications (`type: importer`); use kelvin-sdk-importer instead.
---

# Kelvin SDK — SmartApp Developer

Build and modify Kelvin SmartApps (`type: app`) with the Kelvin Python SDK. Start with a minimal working implementation, then expand only for explicit requirements.

## Execution Workflow

1. Clarify only missing requirements.
2. Identify app shape (stream-only, windows, recommendations/actions, API client usage).
3. Choose the first reference file with deterministic decision rules.
4. Load only additional references required by the task.
5. Implement with the decorator-based runtime model.
6. Write or update tests with the `kelvin-sdk-testing` skill — every handler added or changed must be covered.
7. Validate against the rule checklist before finalizing.

## Mandatory Companion Skill: Testing

Every SmartApp generated, modified, or reviewed under this skill MUST also be covered by tests written with the `kelvin.testing` framework. Load the [`kelvin-sdk-testing`](../kelvin-sdk-testing/SKILL.md) skill in the same turn and produce:

- `tests/__init__.py` (empty)
- `tests/test_main.py` (one `TestXxx` class per handler / behavior)
- `pytest.ini` with `pythonpath = .`

When modifying an existing app, update the test file so every new or changed handler has at least one test, and run `pytest` from the app directory to confirm green before finishing.

## Clarify Missing Requirements

Ask at most 2-3 high-impact questions at a time, and only when missing information blocks correct implementation.

Prioritize questions in this order:
- Inputs: stream names, data types, and target assets.
- Outputs: data streams and/or recommendation/control/custom-action/data-quality outputs.
- Behavior: thresholds, logic, cadence, and expiration/timeout requirements.
- Configuration: app-level configuration vs per-asset parameters.
- Delivery constraints: standalone outputs vs outputs embedded in recommendations.

If the request is already explicit enough, proceed without extra questions.

When details are missing but non-blocking, proceed with explicit assumptions and mark them clearly:
- Use placeholder names like `input_stream`, `output_stream`, and `threshold` only when concrete names are unknown.
- Prefer configurable values (parameters/app configuration) over hardcoded constants.
- Do not invent credentials, KRN identifiers, or environment-specific secrets.
- Summarize all assumptions in the final response so they can be confirmed quickly.

## First-File Decision Rules

Pick exactly one first reference file from the list below, then expand only if needed:
- `app.yaml` declarations, defaults, UI schema wiring, or naming mismatches: [references/app-yaml.md](references/app-yaml.md)
- Lifecycle/decorators/runtime callback behavior/scheduled tasks: [references/sdk-patterns.md](references/sdk-patterns.md)
- Windowing, DataFrame aggregation, or shared state races: [references/data-processing.md](references/data-processing.md)
- Output message classes, recommendations, control changes, custom actions, data tags (also known as data labels), or evidences: [references/messages-outputs.md](references/messages-outputs.md)
- Kelvin API reads/writes (`app.api`) or timeseries queries: [../kelvin-sdk/references/api-client.md](../kelvin-sdk/references/api-client.md)
- KRN construction/parsing: [../kelvin-sdk/references/krn.md](../kelvin-sdk/references/krn.md)
- Ambiguous runtime failures or mixed-category bugs: [../kelvin-sdk/references/best-practices.md](../kelvin-sdk/references/best-practices.md)

Do not load all references by default. Load only what the current task needs.

## Implementation Defaults

- Use decorator-based API (`@app.stream()`, `@app.timer()`, `@app.schedule()`, `@app.task`) unless explicitly asked for function-based patterns.
- Use decorator-style callbacks (`@app.on_connect`, `@app.on_asset_input`, etc.) instead of assignment style.
- Prefer `@app.schedule()` over `@app.timer()` for cron-like recurring tasks with specific times of day or timezone requirements.
- Prefer small explicit handlers with clear stream and asset names.
- Keep business validation in app logic. Rely on framework guarantees for SDK-managed fields.
- Use per-asset parameters for asset-specific behavior and `app.app_configuration` for global behavior.
- Use windows only when aggregation is required. Process streams directly otherwise.
- Keep recommendations and actions minimal and explicit. Add evidences only when requested or clearly useful.

## Validation Checklist

### `app.yaml` and schema alignment

- Declare all published outputs in `app.yaml` before publishing.
- Do not introduce a `configuration:` declaration. Use `defaults.configuration` for global values.
- Keep parameter names identical between `app.yaml` and `ui_schemas`.
- Follow naming conventions: app names with hyphens, stream/parameter names with underscores (or dots when required).

### Messages and actions

- Embed control changes and custom actions inside `Recommendation` unless explicitly asked to publish them standalone.
- Add and use `kelvin_closed_loop` when recommendations carry actions that may auto-accept.
- Set explicit expiration/timeouts for control changes and action-like recommendations.

### Window and data handling

- Always provide `inputs=[...]` for windows.
- Read DataFrame columns by input stream names.
- Guard with `df.empty` and handle NaN values explicitly.
- Use Timeseries API for historical data (typically older than 12 hours), not as `data_streams.inputs`.

### Reliability and safety

- Treat `@app.task` exceptions as fatal unless caught. Add error handling in long-running tasks.
- Log decisions and threshold crossings with asset and stream context.
- Keep secrets and credentials out of source files.

### Tests (via `kelvin-sdk-testing`)

- `tests/test_main.py`, `tests/__init__.py`, and `pytest.ini` exist.
- Every `@app.stream` / `@app.timer` / `@app.schedule` / `@app.task` / `@app.window` / `on_*` callback has at least one test.
- Every output type the app publishes is asserted in at least one test (`isinstance(o, Number | Boolean | String | RecommendationMsg | ControlChangeMsg | CustomActionMsg | DataTagMsg)` — primitives have no separate `*Msg` class — or a resource / payload check).
- All async tests use `@pytest.mark.asyncio` and `async with KelvinAppTest(app, manifest=...) as harness:`.
- Virtual time only: no real `asyncio.sleep`, no `datetime.now()`. Use `harness.clock.now()` and `run_until_idle(...)` / `advance_time(...)`.
- Manifest declares every datastream, asset, control change, and custom action the test touches.

## Framework Guarantees

Assume these SDK guarantees and avoid redundant checks:
- `msg.resource.asset` is a non-null `str` present in `app.assets`.
- `msg.resource.data_stream` is a non-null `str`.
- `msg.payload` matches the type declared in `app.yaml`.

Validate domain and business rules (thresholds, ranges, state transitions), not framework invariants.
