# ManifestBuilder Reference

## When to Use

Use this file to build the `RuntimeManifest` argument for `KelvinAppTest`. The manifest declares which assets, inputs, outputs, control changes, custom actions, and configuration the harness exposes to the app under test. If the harness doesn't know about a datastream or an asset, the app silently drops messages to/from it.

For test file layout and assertions, see [test-patterns.md](test-patterns.md).
For driving inputs from sources, see [data-sources.md](data-sources.md).

## Table of Contents

- [Core Rules](#core-rules)
- [Choosing How to Build](#choosing-how-to-build)
- [Factory Methods](#factory-methods)
  - [`from_app_yaml`](#from_app_yaml)
  - [`from_dict`](#from_dict)
- [Fluent API](#fluent-api)
  - [Data Streams](#data-streams)
  - [Control Changes](#control-changes)
  - [Custom Actions](#custom-actions)
  - [Assets](#assets)
  - [Configuration](#configuration)
- [`build()`](#build)
- [Recipes](#recipes)

## Core Rules

- The manifest must declare EVERY datastream the app reads or writes in the test. A missing declaration means the harness drops the message.
- Datastream and asset names MUST match `main.py`'s `KRNAssetDataStream(asset, datastream)` exactly.
- Valid `data_type` values: `"boolean"`, `"number"`, `"object"`, `"string"`. Anything else raises `ValueError`.
- Always end the chain with `.build()` before passing to `KelvinAppTest`.
- Prefer `ManifestBuilder.from_app_yaml()` when an `app.yaml` already exists — it keeps the test in sync with the spec.
- When testing parameterized behavior, set per-asset parameters via `add_asset(..., parameters={...})` rather than mutating module state.
- `ManifestBuilder().build()` with zero datastreams and zero assets is valid — useful for `@app.schedule`-only apps that have no I/O.
- `on_asset_change` and `on_app_configuration` do NOT fire on the initial manifest — see [test-patterns.md → Triggering callbacks](test-patterns.md#triggering-callbacks-that-need-a-manifest-update) for the manifest-republication recipe.

## Choosing How to Build

| Situation                                            | Approach                                                   |
| ---------------------------------------------------- | ---------------------------------------------------------- |
| `app.yaml` exists and is authoritative               | `ManifestBuilder.from_app_yaml(Path("app.yaml"))` (then extend with assets/params) |
| Generating tests for a brand-new app                 | Fluent API: `ManifestBuilder().add_input(...).add_output(...).add_asset(...)` |
| Fixture loaded from a JSON/dict snapshot             | `ManifestBuilder.from_dict({...})`                         |

## Factory Methods

### `from_app_yaml`

```python
from pathlib import Path
from kelvin.testing import ManifestBuilder

builder = ManifestBuilder.from_app_yaml(Path("app.yaml"))   # path defaults to "app.yaml"
manifest = (
    builder
    .add_asset("pump-001", parameters={"threshold": 80})
    .build()
)
```

- Reads modern spec (`data_streams: {inputs, outputs}`, `control_changes: {inputs, outputs}`) and legacy top-level `inputs:`/`outputs:`.
- Populates `configuration` from `defaults.configuration` or top-level `configuration`.
- Does NOT add assets — always call `.add_asset(...)` for each asset the test needs.
- Raises `FileNotFoundError` when the path is missing.

### `from_dict`

```python
manifest = ManifestBuilder.from_dict({
    "resources":    [{"name": "pump-001", "parameters": {"threshold": 80}}],
    "datastreams":  [
        {"name": "temperature", "data_type": "number", "way": "input"},
        {"name": "alert",       "data_type": "boolean", "way": "output"},
    ],
    "configuration": {"window_size": 60},
}).build()
```

## Fluent API

All `add_*` and `set_*` methods return `Self` so they can be chained. Adding a datastream / asset / custom action with a name that already exists overwrites the previous definition.

### Data Streams

| Method                            | When to use                                                     |
| --------------------------------- | --------------------------------------------------------------- |
| `add_input(name, data_type=...)`  | Datastream the app **reads** with `@app.stream` / window / callbacks |
| `add_output(name, data_type=...)` | Datastream the app **writes** (`harness.outputs` receives them) |
| `add_control_change_input(name)`  | Datastream the app **receives** control changes on (`on_control_change`) |
| `add_control_change_output(name)` | Datastream the app **sends** control changes to                 |
| `add_input_cc_output(name)`       | Owned datastream, both control input and regular output         |
| `add_input_output_cc(name)`       | Remote datastream, both input and control output                |
| `add_datastream(name, data_type, way=WayEnum.input, unit=None, configuration=None)` | Lower-level variant when none of the conveniences fit |

```python
from kelvin.testing import ManifestBuilder

manifest = (
    ManifestBuilder()
    .add_input("temperature", "number", unit="celsius")
    .add_input("running", "boolean")
    .add_output("alert", "boolean")
    .add_output("smoothed-temperature", "number")
    .build()
)
```

### Control Changes

```python
manifest = (
    ManifestBuilder()
    .add_control_change_input("setpoint")          # app receives CC here
    .add_control_change_output("setpoint")         # app sends CC here
    .add_asset("pump-001")
    .build()
)
```

### Custom Actions

```python
from kelvin.testing import ManifestBuilder

manifest = (
    ManifestBuilder()
    .add_custom_action_input("start-pump")          # app handles CA via on_custom_action
    .add_custom_action_output("start-pump-result")  # app responds with this CA
    .add_asset("pump-001")
    .build()
)
```

### Assets

```python
manifest = (
    ManifestBuilder()
    .add_asset("pump-001")
    .add_asset(
        "pump-002",
        properties={"location": "site-a"},
        parameters={"threshold": 80, "kelvin-closed-loop": False},
    )
    .build()
)
```

- `properties` are static metadata (location, model, etc.).
- `parameters` are tunable values the app reads with `get_asset_parameter(...)`.
- The `kelvin-closed-loop` parameter controls whether recommendations auto-accept; set `False` to assert the manual path.

Bulk variant:

```python
ManifestBuilder().add_assets([
    {"name": "pump-001"},
    {"name": "pump-002", "parameters": {"threshold": 80}},
])
```

### Configuration

```python
manifest = (
    ManifestBuilder()
    .set_configuration({"window_size": 60, "threshold": 80})
    .build()
)
```

This is the top-level app configuration (`app.configuration` in `main.py`), not per-asset parameters.

## `build()`

Returns a `RuntimeManifest`. Always call it last:

```python
manifest = ManifestBuilder().add_input("x").add_output("y").add_asset("a").build()
harness = KelvinAppTest(app, manifest=manifest)
```

Do NOT pass the builder itself to `KelvinAppTest`.

## Recipes

### Recipe: A stream handler with a per-asset parameter

```python
def _build_manifest() -> ManifestBuilder:
    return (
        ManifestBuilder()
        .add_input("temperature", "number")
        .add_output("alert", "boolean")
        .add_asset("pump-001", parameters={"threshold": 50})
        .add_asset("pump-002", parameters={"threshold": 80})
    )
```

### Recipe: A timer publishing a recommendation that embeds a control change

```python
def _build_manifest() -> ManifestBuilder:
    return (
        ManifestBuilder()
        .add_control_change_output("output-cc-number", "number")
        .add_asset("asset1", parameters={"kelvin-closed-loop": False})
    )
```

### Recipe: A custom action handler

```python
def _build_manifest() -> ManifestBuilder:
    return (
        ManifestBuilder()
        .add_custom_action_input("start-pump")
        .add_custom_action_output("start-pump-result")
        .add_asset("pump-001")
    )
```

### Recipe: Tumbling window over an input

```python
def _build_manifest() -> ManifestBuilder:
    return (
        ManifestBuilder()
        .add_input("motor-temperature", "number")
        .add_asset("pump-001")
    )
```

### Recipe: Reuse `app.yaml`, only add assets in tests

```python
from pathlib import Path

def _build_manifest() -> ManifestBuilder:
    return (
        ManifestBuilder.from_app_yaml(Path("app.yaml"))
        .add_asset("pump-001")
        .add_asset("pump-002")
    )
```
