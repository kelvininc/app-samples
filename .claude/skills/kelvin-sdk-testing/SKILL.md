---
name: kelvin-sdk-testing
description: Use whenever a Kelvin SmartApp (`type: app`) is created, modified, or reviewed to design and write unit tests with the `kelvin.testing` framework (`KelvinAppTest`, `ManifestBuilder`, data sources). Every SmartApp produced or changed by an agent MUST be accompanied by tests written with this framework; load this skill alongside `kelvin-sdk-app`. Do NOT use for importer applications (`type: importer`).
---

# Kelvin SDK — Testing Framework

Write deterministic, fast, programmatic tests for Kelvin SmartApps using the `kelvin.testing` framework. The framework replaces real I/O with in-memory streams and wall-clock time with a `VirtualClock`, so timers, windows, and schedules fire instantly under `pytest`.

## When to Apply

Apply this skill **every time** a SmartApp is generated, scaffolded, or changed:

- New app generated → create `tests/test_main.py` and `pytest.ini` alongside `main.py`.
- New handler added (`@app.stream`, `@app.timer`, `@app.schedule`, `@app.task`, `@app.window`, callbacks) → add a test class covering that handler.
- Output type added (data, recommendation, control change, custom action, data tag, data quality) → add an assertion that the right `*Msg` is published.
- Logic changed (threshold, configuration, parameter) → add or update a test that exercises the new branch.
Never leave an app without tests. If the user does not explicitly request tests, still produce them — say so briefly in the final summary.

## Execution Workflow

1. Read `main.py` and `app.yaml` to identify handlers, inputs, outputs, assets, parameters, and configuration.
2. Pick one test pattern per handler from the decision table below.
3. Build a manifest with `ManifestBuilder` that mirrors `app.yaml` (or load it via `ManifestBuilder.from_app_yaml(...)`).
4. Inside `async with KelvinAppTest(app, manifest=...) as harness:` publish inputs, advance virtual time with `run_until_idle`, then assert on `harness.outputs`.
5. Run `pytest` from the app directory to confirm tests pass before finishing.

## First-File Decision Rules

Pick exactly one first reference, then expand only if needed:

- Writing the test file, test class layout, pytest config, async fixtures, asserting on outputs by message type, time control, capturing logs: [references/test-patterns.md](references/test-patterns.md)
- Building a `RuntimeManifest` (inputs, outputs, control changes, custom actions, assets, parameters, configuration, loading from `app.yaml`): [references/manifest-builder.md](references/manifest-builder.md)
- Driving inputs from CSV files, synthetic waveforms, random generators, or a DataFrame: [references/data-sources.md](references/data-sources.md)

For SmartApp semantics (decorators, message types, KRN, `app.yaml`), load the matching reference from the `kelvin-sdk-app` skill.

## Required File Layout

For every SmartApp the agent produces, also create:

```
<app-root>/
├── main.py
├── app.yaml
├── pytest.ini                # see pytest.ini block below
└── tests/
    ├── __init__.py           # empty file
    └── test_main.py          # one TestClass per handler / behavior
```

`pytest.ini` (verbatim):

```ini
[pytest]
pythonpath = .
```

This makes `from main import app` resolvable when `pytest` runs from the app directory.

## Canonical Test Skeleton

Use this as the starting point for every new `tests/test_main.py`. Adjust manifest, handler names, and assertions to the app under test.

```python
"""Tests for the <app name> SmartApp."""

from __future__ import annotations

import pytest
from main import app

from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number
from kelvin.testing import KelvinAppTest, ManifestBuilder


def _build_manifest() -> ManifestBuilder:
    # Mirror inputs/outputs/control_changes/custom_actions from app.yaml.
    return (
        ManifestBuilder()
        .add_input("temperature", "number")
        .add_output("alert", "boolean")
        .add_asset("pump-001", parameters={"limit": 50})
    )


def _task_names(a: KelvinApp) -> set[str]:
    """Return short handler names (last segment of dotted keys)."""
    return {k.rsplit(".", 1)[-1] for k in a.tasks}


class TestRegistration:
    """Sanity-check that main.py registers the expected handlers."""

    def test_handlers_registered(self) -> None:
        assert _task_names(app) == {"process_temperature"}


class TestProcessTemperature:
    @pytest.mark.asyncio
    async def test_emits_alert_above_threshold(self) -> None:
        harness = KelvinAppTest(app, manifest=_build_manifest().build())
        async with harness:
            await harness.publish(
                Number(resource=KRNAssetDataStream("pump-001", "temperature"), payload=75.0)
            )
            await harness.run_until_idle()

        alerts = [o for o in harness.outputs if o.resource.data_stream == "alert"]
        assert len(alerts) == 1
        assert alerts[0].payload is True
```

## Handler → Test Pattern Decision Table

| Handler in `main.py`                                  | Manifest must include                                                | Drive the test with                                          | Assert on                                                  |
| ----------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------- |
| `@app.stream(...)` / `on_asset_input`                 | input datastream(s) + asset(s)                                       | `harness.publish(Number(...))` then `run_until_idle()`       | filtered `harness.outputs` by `data_stream` or `isinstance` |
| `@app.timer(interval=N)`                              | output datastream(s) only                                            | `harness.run_until_idle(timeout=N+1)`                        | output count / payload values                              |
| `@app.schedule(...)` (cron)                           | output datastream(s) only                                            | `harness.advance_time(seconds=...)` or `run_until_idle(...)` | output count at expected boundaries                        |
| `@app.task` (one-shot or virtual-clock periodic)      | whatever the task publishes                                          | `run_until_idle()` or `advance_time(...)`                    | outputs / logs via `capsys`                                |
| `@app.task` with `while True` + `asyncio.sleep(rate)` | whatever the task publishes; expose `rate` as an asset parameter or app configuration | set `rate=0` in the manifest, then `run_until_idle(timeout=...)` to bound real time | outputs / logs via `capsys`                                |
| `@app.window(...)` (tumbling/hopping/rolling)         | input(s) + asset(s); set `add_input(..., "number")` etc.             | `publish` N messages with timestamps, then `run_until_idle(timeout > window_size)` | `capsys` or output messages from the window callback        |
| `on_control_change`                                   | `add_control_change_input(...)`                                      | `harness.publish(ControlChange(...))`                        | `ControlChangeMsg` ack or downstream outputs               |
| `on_custom_action`                                    | `add_custom_action_input(...)` (+ outputs if app replies)            | `harness.publish(CustomAction(...))`                         | `CustomActionResultMsg` / outputs                          |
| Publishes recommendations                             | output datastream(s); add `parameters={"kelvin-closed-loop": False}` if applicable | trigger handler                                              | `RecommendationMsg` (and embedded actions)                 |
| Publishes data tags                                   | input(s) + asset(s) (data tags reference inputs as context)          | trigger handler                                              | `DataTagMsg` fields                                        |
| Publishes data quality                                | output datastream(s) + asset(s)                                      | trigger handler                                              | `msg.resource` is `KRNAssetDataQuality` / `KRNAssetDataStreamDataQuality` |

## Implementation Defaults

- Mark every async test with `@pytest.mark.asyncio`. Add `asyncio_mode = auto` to `pytest.ini` only when the user opts in; otherwise mark explicitly.
- Always use `async with KelvinAppTest(app, manifest=...) as harness:` so connect/disconnect is clean and sources stop.
- After publishing, ALWAYS `await harness.run_until_idle(...)` before reading `harness.outputs`.
- Choose `timeout=` to slightly exceed the slowest virtual delay involved (timer interval, window size, schedule gap). Default `5.0` is enough only when no timers/windows are involved.
- Build manifests with the same datastream names and asset names declared in `app.yaml`. Mismatches cause silent drops.
- Use `parameters={...}` on `add_asset(...)` to exercise per-asset behavior; use `set_configuration({...})` for app-level configuration.
- Filter outputs with `isinstance(o, Number | Boolean | String | RecommendationMsg | ControlChangeMsg | CustomActionMsg | DataTagMsg)` (primitives have no separate `*Msg` class) rather than positional indexing. See the message-class cheat sheet in [references/test-patterns.md](references/test-patterns.md).
- Capture stdout/log decisions with the pytest `capsys` fixture when the handler only logs.
- Reset module-level counters or singletons that the app uses between tests. Define a small helper in the test file and call it at the top of each test, e.g. `def _reset_counter() -> None: import main; main.counter = 0`.
- Keep test code deterministic: never call `asyncio.sleep`, `time.sleep`, `datetime.now()`, or `datetime.utcnow()` from inside your test — use `await harness.run_until_idle(timeout=...)` / `await harness.advance_time(...)` and `harness.clock.now()`. If the app under test uses `asyncio.sleep(N)` internally, that sleep is real time (see "What is NOT virtualised" below) — drive `N` through an asset parameter or app configuration and set it to `0` in tests.

## Validation Checklist

Before finishing, confirm all of the following:

- [ ] `tests/__init__.py` exists (empty).
- [ ] `pytest.ini` with `pythonpath = .` exists.
- [ ] Every `@app.stream`/`@app.timer`/`@app.schedule`/`@app.task`/`@app.window`/`on_*` callback has at least one test.
- [ ] Every published output type has at least one assertion (`isinstance`, payload value, or resource check).
- [ ] All async tests are decorated with `@pytest.mark.asyncio`.
- [ ] `ManifestBuilder` declares every input, output, control change, custom action, and asset the test uses.
- [ ] No real-time `sleep`; virtual time is advanced via `run_until_idle` or `advance_time`.
- [ ] Tests run under `pytest` from the app directory (`cd <app>; pytest`).

## Framework Guarantees

When asserting, rely on these guarantees and don't re-test them:

- `harness.outputs` returns every message the app published since connect, in publish order.
- `harness.inputs` returns every message injected (via `publish` or sources), in injection order.
- `harness.clock` is a `VirtualClock`; `harness.clock.now()` is the current virtual `datetime`.
- Sources added with `harness.add_source(source)` start on connect and stop on disconnect.
- `run_until_idle(timeout=T)` drains queues, advances virtual time up to `T` seconds, and waits for handler tasks to finish (subject to a small real-time guard for thread-based sync handlers).
- `harness.outputs` accumulates across multiple `run_until_idle` calls within the same `async with` block; reading it is safe to repeat.
- Background `@app.task` coroutines are cancelled on harness `__aexit__` — an infinite `while True` task does not hang the test.
- `on_asset_change` and `on_app_configuration` never fire on the initial manifest — the harness's initial-manifest delivery establishes the baseline silently. To fire them you must publish a second manifest via `harness.publish(builder.build())`. The two callbacks differ in what counts as a trigger after the initial manifest: `on_asset_change` fires on **any** subsequent manifest (no diff check in the SDK), but `on_app_configuration` fires **only when the configuration value actually changed** (the SDK does `if configuration != self.app_configuration:` before invoking it). For `on_app_configuration`, start the initial manifest with an empty or different configuration, then re-publish with the desired configuration. See [references/test-patterns.md → Triggering callbacks](references/test-patterns.md#triggering-callbacks-that-need-a-manifest-update).
- `ControlChange` *arriving* at the app is a `KMessageTypeData` primitive on an `input_cc` / `input_cc_output` datastream — not a `ControlChangeMsg`. Publish a `Number` (or other primitive) on the CC-declared datastream, not the `ControlChange` builder.
- `ControlAck(...)` produces `ControlChangeAck` (NOT `ControlChangeMsg`). Filter outputs with the correct class.
- `RecommendationMsg.payload.actions.control_changes` / `.custom_actions` hold what the builder takes as top-level `control_changes=` / `actions=`. `auto_accepted=True` becomes `payload.state == "auto_accepted"`. See the builder→payload table in [references/test-patterns.md](references/test-patterns.md#builder-field--wire-format-payload-path).
- `CustomActionMsg`: the action's user-defined `type` lives on `msg.type.type`, not `msg.payload`.
- `@app.schedule` requires an initial `run_until_idle()` *before* `advance_time(...)` so the scheduler task can start.
- `@app.stream()` populates `app._filters` lazily on the first message. For registration checks, use `app.tasks` (the stream is registered there).
- `capfd` (fd-level capture), not `capsys`, captures `print(..., flush=True)` from background tasks (`@app.task`, `@app.schedule`, `@app.stream()`).

### What is NOT virtualised

The harness replaces stream I/O and (for timer/window/schedule primitives) wall-clock time. Everything else runs for real. Be explicit about these in tests:

- **`asyncio.sleep(N)` is real time.** Only timers, windows, and schedules advance under `VirtualClock`. If `main.py` does `await asyncio.sleep(rate)` inside a task, the test waits real seconds. Drive `rate` through a parameter and set it to `0` in tests.
- **File I/O is real.** If `main.py` opens `dataset.csv`, `config.json`, etc. via `Path(__file__).parent / ...`, the test runs against the actual file on disk. To inject fixtures without touching the shipped file, monkeypatch the path:

  ```python
  def test_with_fixture(monkeypatch, tmp_path):
      import main
      fixture = tmp_path / "dataset.csv"
      fixture.write_text("number,1,2,3\n")
      monkeypatch.setattr(main, "DATASET_PATH", fixture)
      ...
  ```

- **Network/HTTP/DB calls are real.** Mock them with `unittest.mock` or `pytest-mock` as you would in any other Python test.
