# SDK Patterns Reference

## When to Use

Use this file to build SmartApp runtime behavior: imports, lifecycle callbacks, stream decorators, timers, tasks, and error-handling patterns.

## Table of Contents
- [SDK Patterns Reference](#sdk-patterns-reference)
  - [When to Use](#when-to-use)
  - [Table of Contents](#table-of-contents)
  - [Core Imports](#core-imports)
  - [Lifecycle and Callbacks](#lifecycle-and-callbacks)
  - [Extended Callbacks](#extended-callbacks)
  - [Decorator-Based Streams](#decorator-based-streams)
  - [Tasks and Timers](#tasks-and-timers)
  - [Scheduled Tasks](#scheduled-tasks)
  - [Function-Based Pattern](#function-based-pattern)
  - [Error Handling](#error-handling)
  - [Framework Guarantees](#framework-guarantees)

## Core Imports

Use minimal imports per task. Add message/resource types only when needed.

```python
from kelvin.application import KelvinApp, filters
from kelvin.logs import logger
from kelvin.message import AssetDataMessage
from kelvin.krn import KRNAssetDataStream

app = KelvinApp()
client = app.api
```

For detailed message classes and payload examples, use [messages-outputs.md](messages-outputs.md).
For KRN constructors and parsing, use [krn.md](krn.md).

## Lifecycle and Callbacks

`app.run()` connects and blocks the current thread.
Use `await app.connect()` only when managing the event loop manually.

Startup order:
1. Connect to platform.
2. Load assets/parameters into `app.assets`.
3. Call `on_connect`.
4. Activate streams/timers/tasks/schedules.

Callbacks support two styles — **decorator** (preferred) and assignment:

```python
from typing import Optional
from kelvin.application import AssetInfo

# Decorator style (preferred)
@app.on_connect
async def on_connect() -> None:
    logger.info("Connected", assets=list(app.assets.keys()))

@app.on_asset_change
async def on_asset_change(new_asset: Optional[AssetInfo], old_asset: Optional[AssetInfo]) -> None:
    if new_asset is not None:
        logger.info("Asset added/updated", asset=new_asset.name)
    elif old_asset is not None:
        logger.info("Asset removed", asset=old_asset.name)

# Assignment style (backwards compatible)
# app.on_connect = on_connect
# app.on_asset_change = on_asset_change
```

## Extended Callbacks

Use these callbacks when your app handles control workflows, custom actions, or runtime configuration updates.
All callbacks support both decorator and assignment styles.

```python
from kelvin.message import AssetDataMessage, ControlAck, ControlChangeStatus, CustomAction, DataTag, StateEnum

@app.on_asset_input
async def on_asset_input(msg: AssetDataMessage) -> None:
    logger.info("Asset input", resource=str(msg.resource), payload=msg.payload)

@app.on_control_change
async def on_control_change(msg: AssetDataMessage) -> None:
    logger.info("Control change received", resource=str(msg.resource), payload=msg.payload)
    ack = ControlAck(resource=msg.resource, state=StateEnum.applied, message="Applied successfully")
    await app.publish(ack)

@app.on_control_status
async def on_control_status(status: ControlChangeStatus) -> None:
    logger.info("Control change status", status=status)

@app.on_custom_action
async def on_custom_action(action: CustomAction) -> None:
    logger.info("Custom action received", action=action)

@app.on_data_tag
async def on_data_tag(tag: DataTag) -> None:
    logger.info("Data tag received", tag_name=tag.tag_name, resource=str(tag.resource))

@app.on_app_configuration
async def on_app_configuration(conf: dict) -> None:
    logger.info("App configuration changed", config=conf)
```

Available callback slots:

| Callback | Argument | Purpose |
|----------|----------|---------|
| `on_connect` | *(none)* | Connection established |
| `on_disconnect` | *(none)* | Connection closed |
| `on_message` | `Message` | Any message received |
| `on_asset_input` | `AssetDataMessage` | Asset data message |
| `on_control_change` | `AssetDataMessage` | Control change received |
| `on_control_status` | `ControlChangeStatus` | Control change status |
| `on_custom_action` | `CustomAction` | Custom action received |
| `on_data_tag` | `DataTag` | Data tag received |
| `on_asset_change` | `Optional[AssetInfo], Optional[AssetInfo]` | Asset added/removed/updated |
| `on_app_configuration` | `dict` | App configuration changed |

## Decorator-Based Streams

Use decorators by default.

```python
@app.stream(assets=["asset-1"], inputs=["casing_pressure", "oil_rate"])
async def monitor(msg: AssetDataMessage) -> None:
    asset = msg.resource.asset
    stream = msg.resource.data_stream
    value = msg.payload

    logger.info("Stream message", asset=asset, stream=stream, value=value)

    if stream == "casing_pressure":
        max_pressure = float(app.assets[asset].parameters.get("max_casing_pressure", 1500.0))
        if value > max_pressure:
            logger.warning("Pressure limit exceeded", asset=asset, value=value, limit=max_pressure)
```

## Tasks and Timers

Use `@app.task` for background processing and window loops.
Use `@app.timer` for periodic checks.
Use `@app.schedule` for cron-like recurring tasks (see [Scheduled Tasks](#scheduled-tasks)).

```python
import asyncio

@app.task
async def continuous_check() -> None:
    try:
        while True:
            await asyncio.sleep(10)
            logger.info("Background check")
    except Exception as exc:
        logger.error("Task failed", error=str(exc), exc_info=True)

@app.timer(interval=30)
async def periodic_check() -> None:
    logger.info("Timer tick")
```

## Scheduled Tasks

Use `@app.schedule` for cron-like recurring tasks with timezone support.
Prefer human-readable parameters (`every`/`at`) over raw cron expressions.

**Human-readable schedules:**

```python
@app.schedule(every="day", at="19:00", timezone="Australia/Sydney")
async def daily_report() -> None:
    logger.info("Running daily report")

@app.schedule(every="monday", at="09:00")
async def weekly_standup() -> None:
    logger.info("Weekly standup reminder")

@app.schedule(every="weekday", at="08:00", timezone="Europe/London")
async def weekday_morning() -> None:
    logger.info("Good morning!")

@app.schedule(every="day", at=["09:00", "17:00"])
async def twice_daily() -> None:
    logger.info("Runs at 09:00 and 17:00 UTC")
```

**Cron expressions:**

```python
@app.schedule(cron="*/5 * * * *")
async def every_five_minutes() -> None:
    logger.info("Runs every 5 minutes")

@app.schedule(cron="0 9 * * MON-FRI", timezone="Europe/London")
async def weekday_job() -> None:
    logger.info("Weekday 9am London")
```

**Interval (every Nth occurrence):**

```python
from datetime import datetime, timezone

@app.schedule(
    every="monday",
    at="09:00",
    interval=2,
    start_time=datetime(2026, 4, 6, tzinfo=timezone.utc),
)
async def biweekly_sync() -> None:
    logger.info("Runs every other Monday at 09:00 UTC")
```

**Schedule parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `every` | `str` | Human-readable recurrence: `"day"`, `"monday"`–`"sunday"`, `"weekday"`, `"weekend"` |
| `at` | `str \| list[str]` | Time(s) in `"HH:MM"` format. Required with `every`. |
| `cron` | `str` | Standard 5-field cron expression. Mutually exclusive with `every`/`at`. |
| `timezone` | `str` | IANA timezone name (default `"UTC"`). |
| `start_time` | `datetime` | Reference datetime for deterministic schedule alignment. |
| `interval` | `int` | Fire every Nth cron match. |
| `name` | `str` | Optional task name for logging. |

`every`/`at` and `cron` are mutually exclusive — use one or the other.

## Function-Based Pattern

Use only when explicitly requested.

```python
from kelvin.message import Number

async def process_stream(app: KelvinApp) -> None:
    stream = app.stream_filter(filters.input_equals(["temperature"]))
    async for msg in stream:
        await app.publish(
            Number(
                resource=KRNAssetDataStream(msg.resource.asset, "production_index"),
                payload=msg.payload * 2,
            )
        )
```

## Error Handling

| Decorator | Exceptions Handled? | Behavior |
|-----------|---------------------|----------|
| `@app.stream()` | YES | Next message still calls handler |
| `@app.timer()` | YES | Timer continues firing |
| `@app.schedule()` | YES | Schedule continues on next trigger |
| `@app.task` | NO | Task stops if exception escapes |

Wrap long-running `@app.task` loops in `try/except`.

## Framework Guarantees

Assume:
- `msg.resource.asset` is non-null and present in `app.assets`.
- `msg.resource.data_stream` is non-null.
- `msg.payload` type matches `app.yaml` declarations.

Validate business logic (thresholds, ranges, state transitions), not framework invariants.
