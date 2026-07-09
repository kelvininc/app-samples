# Test Patterns Reference

## When to Use

Use this file to write the test file itself: file layout, async/pytest wiring, harness lifecycle, publishing inputs, advancing virtual time, asserting on outputs by message type, capturing logs, and per-handler templates (stream, timer, schedule, window, control change, custom action, recommendation, data tag, data quality).

For manifest construction details, see [manifest-builder.md](manifest-builder.md).
For driving inputs from CSV / synthetic / random sources, see [data-sources.md](data-sources.md).

## Table of Contents

- [Test Patterns Reference](#test-patterns-reference)
  - [When to Use](#when-to-use)
  - [Table of Contents](#table-of-contents)
  - [Core Rules](#core-rules)
  - [Project Layout and Pytest Config](#project-layout-and-pytest-config)
  - [Harness Lifecycle](#harness-lifecycle)
  - [Publishing Inputs](#publishing-inputs)
  - [Virtual Time Control](#virtual-time-control)
  - [Asserting on Outputs](#asserting-on-outputs)
    - [Message Class Cheat Sheet](#message-class-cheat-sheet)
  - [Capturing Logs](#capturing-logs)
  - [Per-Handler Templates](#per-handler-templates)
    - [Stream Handler](#stream-handler)
    - [Timer Handler](#timer-handler)
    - [Schedule Handler](#schedule-handler)
    - [Task Handler](#task-handler)
    - [Window Handler](#window-handler)
    - [Control Change Callback](#control-change-callback)
    - [Custom Action Callback](#custom-action-callback)
    - [Recommendation Publishing](#recommendation-publishing)
    - [Data Tag Publishing](#data-tag-publishing)
    - [Data Quality Publishing](#data-quality-publishing)
  - [Common Pitfalls](#common-pitfalls)

## Core Rules

- One `tests/test_main.py` per app, organised into `TestXxx` classes per handler or behavior.
- Always import the singleton via `from main import app`; never instantiate a second `KelvinApp`.
- Always wrap the harness in `async with KelvinAppTest(app, manifest=...) as harness:`.
- Always `await harness.run_until_idle(...)` before reading `harness.outputs`.
- Choose `timeout=` slightly above the largest virtual delay (timer interval, window size, schedule offset). Default `5.0s` only works when there are no timers/windows.
- Filter outputs by `isinstance(o, <Msg>)` or by `o.resource.data_stream == "..."`.
- Make tests deterministic: never call `time.sleep`, `asyncio.sleep` (real), `datetime.now()`, or `datetime.utcnow()`. Use `harness.clock.now()`.
- Reset module-level state (counters, caches in `main.py`) at the start of each test that depends on it.

## Project Layout and Pytest Config

```
<app-root>/
├── main.py
├── app.yaml
├── pytest.ini
└── tests/
    ├── __init__.py
    └── test_main.py
```

`pytest.ini`:

```ini
[pytest]
pythonpath = .
```

`tests/__init__.py`: empty file.

Run from the app directory:

```bash
cd <app-root>
pytest
```

## Harness Lifecycle

```python
import pytest
from main import app
from kelvin.testing import KelvinAppTest, ManifestBuilder


@pytest.mark.asyncio
async def test_smoke() -> None:
    harness = KelvinAppTest(app, manifest=ManifestBuilder().build())
    async with harness:
        await harness.run_until_idle()
        assert harness.is_connected
    # On exit: app is disconnected, sources stopped, app state restored.
    # Background @app.task coroutines are cancelled — an infinite `while True` does not hang the test.
```

Construction:

```python
KelvinAppTest(app, manifest)                    # auto VirtualClock
KelvinAppTest(app, manifest, clock=my_clock)    # bring your own VirtualClock
```

Adding sources (must be done BEFORE `async with`):

```python
harness = KelvinAppTest(app, manifest=manifest).add_source(source1).add_source(source2)
```

## Publishing Inputs

```python
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message import Number, ControlChange, CustomAction

# Single data message
await harness.publish(
    Number(resource=KRNAssetDataStream("pump-001", "temperature"), payload=55.0)
)

# Batch
await harness.publish_batch([
    Number(resource=KRNAssetDataStream("pump-001", "temperature"), payload=50.0),
    Number(resource=KRNAssetDataStream("pump-001", "temperature"), payload=55.0),
])

# Control change input (use add_control_change_input in the manifest)
from datetime import timedelta
await harness.publish(
    ControlChange(
        resource=KRNAssetDataStream("pump-001", "setpoint"),
        payload=75.0,
        expiration_date=timedelta(minutes=10),
    )
)

# Custom action input (use add_custom_action_input in the manifest)
await harness.publish(
    CustomAction(
        resource=KRNAsset("pump-001"),
        type="start-pump",
        title="Start Pump",
        expiration_date=timedelta(seconds=30),
    )
)
```

`publish` records the message in `harness.inputs` AND injects it into the in-memory stream so the handlers run.

## Virtual Time Control

```python
# Process queues + advance virtual clock up to `timeout` seconds.
# Use this 99% of the time.
await harness.run_until_idle(timeout=61.0)

# Explicit jump without waiting on queues.
await harness.advance_time(seconds=120)

# Read the virtual clock.
now = harness.clock.now()           # datetime
mono = harness.clock.perf_counter() # float seconds
```

Rules:

- For a `@app.timer(interval=N)` use `timeout >= N + 1`.
- For a `@app.window(size=N)` publish all inputs first, then `run_until_idle(timeout=N + buffer)`.
- For multiple firings of the same timer, call `run_until_idle(timeout=k*N + 1)` once or call it in a loop.

## Asserting on Outputs

Primitive messages (`Number`, `Boolean`, `String`) are used for BOTH inputs and outputs — there is no separate `NumberMsg`/`BooleanMsg`/`StringMsg`. Non-primitive output types each have their own `*Msg` class.

```python
from kelvin.message import (
    Number, Boolean, String,
    RecommendationMsg, ControlChangeMsg, CustomActionMsg, CustomActionResultMsg,
    DataTagMsg,
)
from kelvin.message.base_messages import ParametersMsg   # not re-exported from kelvin.message
from kelvin.krn import KRNAssetDataQuality, KRNAssetDataStreamDataQuality

outputs = harness.outputs                       # always read AFTER run_until_idle
                                                # accumulates across run_until_idle calls within one async with block

# Count by type
numbers = [o for o in outputs if isinstance(o, Number)]
recs    = [o for o in outputs if isinstance(o, RecommendationMsg)]
acks    = [o for o in outputs if isinstance(o, ControlChangeMsg)]

# Filter by data stream (most common)
alerts = [o for o in outputs if o.resource.data_stream == "alert"]

# Filter by asset
for_pump1 = [o for o in outputs if o.resource.asset == "pump-001"]

# Filter by KRN type (data quality)
asset_quality  = [o for o in outputs if isinstance(o.resource, KRNAssetDataQuality)]
stream_quality = [o for o in outputs if isinstance(o.resource, KRNAssetDataStreamDataQuality)]

# Assert on payload
assert numbers[0].payload == 24.0
assert recs[0].payload.type == "alert"
```

### Message Class Cheat Sheet

| Published by app                            | Class to filter with                                | Import from                          |
| ------------------------------------------- | --------------------------------------------------- | ------------------------------------ |
| `Number(...)`                               | `Number`                                            | `kelvin.message`                     |
| `Boolean(...)`                              | `Boolean`                                           | `kelvin.message`                     |
| `String(...)`                               | `String`                                            | `kelvin.message`                     |
| `Recommendation(...)`                       | `RecommendationMsg`                                 | `kelvin.message`                     |
| `ControlChange(...)`                        | `ControlChangeMsg`                                  | `kelvin.message`                     |
| `ControlAck(...)`                           | `ControlChangeAck` (NOT `ControlChangeMsg`)         | `kelvin.message`                     |
| `CustomAction(...)`                         | `CustomActionMsg`                                   | `kelvin.message`                     |
| `CustomActionResult(...)`                   | `CustomActionResultMsg`                             | `kelvin.message`                     |
| `DataTag(...)`                              | `DataTagMsg`                                        | `kelvin.message`                     |
| `AssetParameters(...)` / `AppParameters(...)` | `ParametersMsg`                                   | `kelvin.message.base_messages`       |
| Data quality (`Number` with quality KRN)    | `Number` + `isinstance(o.resource, KRNAssetDataQuality \| KRNAssetDataStreamDataQuality)` | `kelvin.message` / `kelvin.krn` |

`harness.outputs` is a copy and idempotent; each call also drains any newly-published messages.

> **Payload typing:** primitive payloads preserve the Python type the app published — `int` stays `int`, `float` stays `float`. Use `pytest.approx(...)` when comparing `float` payloads and bare `==` for `int`. Don't write `payload == 1.0` if the app emitted `payload=1`.

### Builder field → wire-format payload path

The builders (`Recommendation`, `ControlChange`, `CustomAction`, …) take ergonomic kwargs, but the wire-format payload that arrives in `harness.outputs` re-nests them. Assert against the wire shape, not the builder kwargs.

| Builder field | Wire payload path | Notes |
| --- | --- | --- |
| `Recommendation(control_changes=[...])` | `msg.payload.actions.control_changes` | nested under `actions` |
| `Recommendation(actions=[CustomAction(...)])` | `msg.payload.actions.custom_actions` | nested under `actions` |
| `Recommendation(auto_accepted=True)` | `msg.payload.state == "auto_accepted"` | not a boolean field |
| `Recommendation(auto_accepted=False)` | `msg.payload.state is None` | unset, not `False` |
| `ControlChange(queueing=ControlChangeQueueing.ENQUEUE)` (inside a Recommendation) | `cc.queueing.mode == ControlChangeQueueing.ENQUEUE` | builder wraps in `QueueingMode(mode=...)` |
| `CustomAction(type="send-email")` | `msg.type.type == "send-email"` | type lives on the message-type wrapper, not the payload |
| `Recommendation(type="generic")` | `msg.payload.type == "generic"` | payload-level *category*, distinct from `msg.type` (wire-format marker) |

## Capturing Logs

Use the built-in `capsys` fixture when the handler only logs:

```python
@pytest.mark.asyncio
async def test_logs_decision(capsys: pytest.CaptureFixture[str]) -> None:
    harness = KelvinAppTest(app, manifest=_build_manifest().build())
    async with harness:
        await harness.run_until_idle(timeout=6.0)

    captured = capsys.readouterr().out
    assert "threshold crossed" in captured
```

> **`capsys` vs `capfd`:** `capsys` may miss `print(..., flush=True)` from background tasks (`@app.task`, `@app.schedule`, `@app.stream()`) because output bypasses the sys-level stream. If the handler under test uses `print(...)` from a background task, switch to `capfd` (fd-level capture) — same API, just `capfd.readouterr().out`.

## Triggering callbacks that need a manifest update

`on_asset_change` and `on_app_configuration` are both guarded by `_config_received.is_set()` in the SDK, so neither ever fires on the initial manifest the harness delivers on connect. They differ in what counts as a trigger after that:

| Callback | Fires on identical re-publish? | SDK guard |
|---|---|---|
| `on_asset_change` | ✅ Yes — fires every time the manifest is re-published | only `_config_received.is_set()` |
| `on_app_configuration` | ❌ No — only when `configuration` actually differs | `_config_received.is_set() AND configuration != self.app_configuration` |

So this — which looks correct — silently does nothing:

```python
manifest = ManifestBuilder().add_asset("pump-001", parameters={"x": 1}).build()
async with KelvinAppTest(app, manifest=manifest) as harness:
    await harness.run_until_idle()   # on_asset_change NEVER fires here
    assert harness.outputs == []     # ← passes, but probably not what you want
```

### `on_asset_change` — any second manifest is enough

```python
def _manifest(parameters: dict) -> ManifestBuilder:
    return (
        ManifestBuilder()
        .add_output("rw-number", "number")
        .add_asset("pump-001", parameters=parameters)
    )

@pytest.mark.asyncio
async def test_emits_on_parameter_update() -> None:
    params = {"threshold": 80}
    harness = KelvinAppTest(app, manifest=_manifest(params).build())
    async with harness:
        await harness.publish(_manifest(params).build())   # ← identical re-publish is fine
        await harness.run_until_idle()

    assert len(harness.outputs) > 0
```

### `on_app_configuration` — the configuration must actually change

The SDK explicitly compares `configuration != self.app_configuration` before invoking the callback, so re-publishing the *same* configuration is a no-op. Start the initial manifest with an empty (or different) configuration, then re-publish with the desired configuration:

```python
def _manifest(config: dict) -> ManifestBuilder:
    return (
        ManifestBuilder()
        .add_output("out-string", "string")
        .add_asset("a-1")
        .set_configuration(config)
    )

@pytest.mark.asyncio
async def test_emits_on_configuration_change() -> None:
    # Initial: empty config. Second: desired config — the diff fires the callback.
    harness = KelvinAppTest(app, manifest=_manifest({}).build())
    async with harness:
        await harness.publish(_manifest({"message_length_bytes": 256}).build())
        await harness.run_until_idle()

    assert len(harness.outputs) >= 1
```

## Per-Handler Templates

### Stream Handler

```python
@pytest.mark.asyncio
async def test_stream_emits_on_threshold() -> None:
    manifest = (
        ManifestBuilder()
        .add_input("temperature", "number")
        .add_output("alert", "boolean")
        .add_asset("pump-001", parameters={"limit": 50})
        .build()
    )
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        await harness.publish(
            Number(resource=KRNAssetDataStream("pump-001", "temperature"), payload=75.0)
        )
        await harness.run_until_idle()

    alerts = [o for o in harness.outputs if o.resource.data_stream == "alert"]
    assert len(alerts) == 1
    assert alerts[0].payload is True
```

### Timer Handler

```python
@pytest.mark.asyncio
async def test_timer_fires_once_per_interval() -> None:
    manifest = ManifestBuilder().add_output("heartbeat", "number").add_asset("a").build()
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        # interval=5 in @app.timer(interval=5)
        await harness.run_until_idle(timeout=6.0)

    assert len([o for o in harness.outputs if o.resource.data_stream == "heartbeat"]) >= 1
```

### Schedule Handler

`@app.schedule` registers as a task that internally `await harness.clock.sleep(N)`s until the next fire time. You must let the task spin up *first* — otherwise `advance_time` has no sleep to wake.

```python
@pytest.mark.asyncio
async def test_schedule_runs_at_boundary(capfd: pytest.CaptureFixture[str]) -> None:
    # An empty manifest is fine for schedule-only apps.
    harness = KelvinAppTest(app, manifest=ManifestBuilder().build())
    async with harness:
        await harness.run_until_idle()             # ① let the scheduler task start
        await harness.advance_time(seconds=3600)   # ② cross the next fire boundary
        await harness.run_until_idle(timeout=2.0)  # ③ wait for the handler to finish

    # capfd (not capsys) — print() from a background task bypasses sys-level capture.
    assert "scheduled hello" in capfd.readouterr().out
```

### Task Handler

`@app.task` coroutines start when the app connects and are cancelled on harness `__aexit__`. There are three common shapes — pick the matching sub-pattern.

#### One-shot task

Runs once, publishes, returns. `run_until_idle` waits ~10 ms for it to finish.

```python
@pytest.mark.asyncio
async def test_task_publishes_on_startup() -> None:
    harness = KelvinAppTest(app, manifest=_build_manifest().build())
    async with harness:
        await harness.run_until_idle()

    assert len(harness.outputs) > 0
```

#### Periodic task that sleeps on the virtual clock

When the task uses `harness.clock` or timer primitives, advance virtual time across each tick.

```python
@pytest.mark.asyncio
async def test_periodic_task_emits_each_cycle() -> None:
    harness = KelvinAppTest(app, manifest=_build_manifest().build())
    async with harness:
        await harness.advance_time(seconds=30)   # cross 3 ticks of a 10s task
        await harness.run_until_idle()

    assert len([o for o in harness.outputs if isinstance(o, Number)]) >= 3
```

#### Infinite `while True` task with real `asyncio.sleep(rate)`

The task loops forever and uses real `asyncio.sleep`. **The harness does NOT replace `asyncio.sleep`** — only timers, windows, and schedules are virtualised. Drive the rate through an asset parameter (or app configuration), set it to `0` in tests so the loop runs tightly, and bound the test with `run_until_idle(timeout=...)`. The task is cancelled cleanly when `async with` exits.

```python
@pytest.mark.asyncio
async def test_infinite_loop_task_publishes_dataset() -> None:
    manifest = (
        ManifestBuilder()
        .add_output("number-datastream", "number")
        .add_asset("asset-1", parameters={"output_type": "number", "publishing_rate": 0})
        .build()
    )
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        await harness.run_until_idle(timeout=2.0)   # bound real time spent in the loop

    numbers = [o for o in harness.outputs if isinstance(o, Number)]
    assert len(numbers) >= 1
```

If `rate` cannot be made configurable, the test will take real wall-clock seconds — refactor the app instead of inflating the test timeout.

### Window Handler

Publish all inputs first, then advance past the window boundary.

```python
from datetime import timedelta

@pytest.mark.asyncio
async def test_tumbling_window_emits() -> None:
    manifest = ManifestBuilder().add_input("motor-temperature", "number").add_asset("pump-001").build()
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        now = harness.clock.now()
        for i in range(1, 6):
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("pump-001", "motor-temperature"),
                    payload=float(i),
                    timestamp=now + timedelta(seconds=i),
                )
            )
        # Window size = 10s; advance past it.
        await harness.run_until_idle(timeout=15.0)

    # Assert on outputs the window callback produced (or capsys for log-only handlers).
```

### Control Change Callback

A control change arriving at the app is just a `KMessageTypeData` message (Number/Boolean/etc.) on a datastream whose `way` is `input_cc` or `input_cc_output`. The SDK's `msg_is_control_change` predicate routes by the datastream's `way`, **not** by the wire-format message type. So:

- **Don't** publish a `ControlChange(...)` builder via `harness.publish(...)` — that produces a `ControlChangeMsg` (`type=control`), which the SDK does NOT route to `on_control_change`.
- **Do** publish a `Number` (or other primitive) on the CC-declared datastream.
- The app's `ControlAck(...)` reply becomes a `ControlChangeAck` (NOT `ControlChangeMsg`) — filter accordingly.

```python
from kelvin.krn import KRNAssetDataStream
from kelvin.message import ControlChangeAck, Number

@pytest.mark.asyncio
async def test_on_control_change_acks() -> None:
    manifest = (
        ManifestBuilder()
        # rw-number is both control input and regular output (echoed by the app).
        .add_input_cc_output("rw-number", "number")
        .add_asset("pump-001")
        .build()
    )
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        await harness.publish(
            Number(resource=KRNAssetDataStream("pump-001", "rw-number"), payload=75.0)
        )
        await harness.run_until_idle()

    acks = [o for o in harness.outputs if isinstance(o, ControlChangeAck)]
    assert len(acks) == 1
```

### Custom Action Callback

```python
from datetime import timedelta
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction, CustomActionResultMsg

@pytest.mark.asyncio
async def test_on_custom_action_succeeds() -> None:
    manifest = (
        ManifestBuilder()
        .add_custom_action_input("start-pump")
        .add_custom_action_output("start-pump-result")
        .add_asset("pump-001")
        .build()
    )
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        await harness.publish(
            CustomAction(
                resource=KRNAsset("pump-001"),
                type="start-pump",
                title="Start Pump",
                expiration_date=timedelta(seconds=30),
            )
        )
        await harness.run_until_idle()

    results = [o for o in harness.outputs if isinstance(o, CustomActionResultMsg)]
    assert len(results) == 1
    assert results[0].payload.success is True
```

### Recommendation Publishing

Recommendations are typically emitted from `on_asset_change` or a timer/task. If the app emits from `on_asset_change`, you must re-publish the manifest to fire the callback (see [Triggering callbacks](#triggering-callbacks-that-need-a-manifest-update)).

When asserting on the recommendation, remember the wire payload nests fields under `actions` and uses `state` instead of a boolean:

```python
from kelvin.message import RecommendationMsg

@pytest.mark.asyncio
async def test_publishes_recommendation_on_param_update() -> None:
    parameters = {"cc_value": 5, "auto_accept": True}
    manifest_builder = (
        ManifestBuilder()
        .add_control_change_output("rw-number", "number")
        .add_asset("asset-1", parameters=parameters)
    )
    harness = KelvinAppTest(app, manifest=manifest_builder.build())
    async with harness:
        # Re-publish manifest to fire on_asset_change.
        await harness.publish(manifest_builder.build())
        await harness.run_until_idle()

    recs = [o for o in harness.outputs if isinstance(o, RecommendationMsg)]
    assert len(recs) == 1
    payload = recs[0].payload
    assert payload.type == "e2e_recommendation"
    assert payload.state == "auto_accepted"                    # auto_accepted=True → state="auto_accepted"
    assert len(payload.actions.control_changes) == 1           # nested under .actions
    assert payload.actions.control_changes[0].payload == 5
```

### Data Tag Publishing

```python
from kelvin.message import DataTagMsg

@pytest.mark.asyncio
async def test_publishes_data_tag() -> None:
    manifest = (
        ManifestBuilder()
        .add_input("input-1", "number")
        .add_asset("pump-001")
        .add_asset("pump-002")
        .build()
    )
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        await harness.run_until_idle(timeout=61.0)

    tags = [o for o in harness.outputs if isinstance(o, DataTagMsg)]
    assert len(tags) == 2
    assert tags[0].payload.tag_name == "test-tag"
```

### Data Quality Publishing

```python
from kelvin.krn import KRNAssetDataQuality, KRNAssetDataStreamDataQuality

@pytest.mark.asyncio
async def test_publishes_quality_scores() -> None:
    manifest = (
        ManifestBuilder()
        .add_output("temperature", "number")
        .add_asset("pump-001")
        .add_asset("pump-002")
        .build()
    )
    harness = KelvinAppTest(app, manifest=manifest)
    async with harness:
        await harness.run_until_idle(timeout=11.0)

    asset_q  = [o for o in harness.outputs if isinstance(o.resource, KRNAssetDataQuality)]
    stream_q = [o for o in harness.outputs if isinstance(o.resource, KRNAssetDataStreamDataQuality)]
    assert len(asset_q)  >= 2
    assert len(stream_q) >= 2
```

## Common Pitfalls

- Reading `harness.outputs` before `run_until_idle` returns → empty list.
- Using a `timeout` smaller than the timer interval / window size → handler never fires.
- Forgetting to declare an output in the manifest → message is dropped silently when the app tries to publish it.
- Publishing to an asset that is not in the manifest → message is dropped.
- Mixing real `asyncio.sleep` with the virtual clock → tests become slow or non-deterministic.
- Forgetting `@pytest.mark.asyncio` → pytest reports the test as a warning and skips its body.
- Re-using a module-level counter across tests without resetting → flaky assertions on payload contents.
- Calling `add_source` AFTER `async with` → raises `RuntimeError`. Always chain before connect.
