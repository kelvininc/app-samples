# Data Sources Reference

## When to Use

Use this file when an app's test needs continuous or pre-recorded input streams instead of single `harness.publish(...)` calls. Sources are time-aware: they yield messages on the `VirtualClock`, so `run_until_idle(timeout=N)` advances them as a coherent batch.

For test layout and assertions, see [test-patterns.md](test-patterns.md).
For declaring the matching inputs / assets in the manifest, see [manifest-builder.md](manifest-builder.md).

## Table of Contents

- [Data Sources Reference](#data-sources-reference)
  - [When to Use](#when-to-use)
  - [Table of Contents](#table-of-contents)
  - [Core Rules](#core-rules)
  - [Choosing a Source](#choosing-a-source)
  - [Lifecycle](#lifecycle)
  - [`CSVSource`](#csvsource)
  - [`SyntheticSource` + Wave Patterns](#syntheticsource--wave-patterns)
  - [`RandomSource`](#randomsource)
  - [`DataFrameSource`](#dataframesource)
  - [`TabularSource`](#tabularsource)
  - [Common Pitfalls](#common-pitfalls)

## Core Rules

- ALWAYS call `harness.add_source(source)` BEFORE entering `async with harness:`. Adding a source after connect raises `RuntimeError`.
- ALWAYS call `.with_asset("<name>")` on the source unless the source provides asset names per-row (e.g., `CSVSource` with `asset_column`).
- The datastreams the source publishes MUST be declared via `add_input(...)` in the manifest.
- Source emission cadence is set per source type, NOT uniformly via `with_timing`:
  - `SyntheticSource` — use the `sample_rate=` constructor argument. `with_timing` has no effect.
  - `RandomSource` — use `with_timing(timedelta(...))` (reads `DataSource._interval`).
  - `CSVSource` / `DataFrameSource` / `TabularSource` — timing comes from the row timestamps; `with_timing` only applies as a fallback when `ignore_timestamps=True`.
- Sources stop automatically on harness exit. Don't manage them manually.
- For deterministic tests, always seed `RandomSource`. CSV / Synthetic / DataFrame are already deterministic.

## Choosing a Source

| Goal                                                                 | Use                                                  |
| -------------------------------------------------------------------- | ---------------------------------------------------- |
| Replay recorded production data from a CSV file                      | `CSVSource`                                          |
| Drive an app with a known mathematical signal (sine, square, ramp, noise, constant) | `SyntheticSource` + a `WavePattern` |
| Fuzz / stress test with bounded random values                        | `RandomSource(..., seed=42)`                         |
| Replay a pandas DataFrame held in memory                             | `DataFrameSource`                                    |
| Custom row-based source                                              | Subclass `TabularSource` or `DataSource`             |

## Lifecycle

```python
from datetime import timedelta
from kelvin.testing import KelvinAppTest, ManifestBuilder
from kelvin.testing.sources import CSVSource, SyntheticSource
from kelvin.testing.sources.synthetic import SineWave

manifest = (
    ManifestBuilder()
    .add_input("temperature", "number")
    .add_input("pressure", "number")
    .add_asset("pump-001")
    .build()
)

harness = (
    KelvinAppTest(app, manifest=manifest)
    .add_source(
        SyntheticSource(
            SineWave(amplitude=10, period=timedelta(seconds=60)),
            "temperature",
            sample_rate=timedelta(seconds=1),   # cadence lives here, NOT in with_timing
        ).with_asset("pump-001")
    )
    .add_source(
        CSVSource("tests/data/pressure.csv", asset_column=None, timestamp_column="ts")
        .with_asset("pump-001")
        .with_columns("pressure")
    )
)

async with harness:
    await harness.run_until_idle(timeout=120.0)   # let both sources stream for 2 virtual minutes
    outputs = harness.outputs
```

## `CSVSource`

Replays rows from a CSV file. One message per (datastream column, row).

```python
from kelvin.testing.sources import CSVSource

source = (
    CSVSource(
        path="tests/data/recording.csv",
        playback="realtime",          # "realtime" follows timestamps; "fast" emits as fast as possible
        ignore_timestamps=False,      # True → spacing falls back to `with_timing(interval)`
        now_offset=None,              # shift all timestamps so first row aligns with clock.now()
        asset_column=None,            # column whose value identifies the asset per row
        timestamp_column="timestamp", # column to read as message timestamp
    )
    .with_asset("pump-001")                       # fallback when asset_column is None
    .with_columns("temperature", "pressure")      # only emit these columns as datastreams
    .with_column_mapping({"temp": "temperature"}) # rename CSV columns to manifest datastream names
)
```

Notes:

- One datastream per non-asset / non-timestamp column. Use `.with_columns(...)` to limit, `.with_column_mapping(...)` to rename.
- When `asset_column` is set, each row carries its own asset and `.with_asset(...)` is only the fallback.
- For window/timer apps, set `now_offset=timedelta(0)` so the first sample lands at `harness.clock.now()`.

## `SyntheticSource` + Wave Patterns

Generates one message per sample tick from a `WavePattern`. The cadence is controlled by the `sample_rate=` constructor argument; `DataSource.with_timing(...)` is inherited but **ignored** by this source.

```python
from datetime import timedelta
from kelvin.testing.sources import SyntheticSource
from kelvin.testing.sources.synthetic import (
    SineWave, SquareWave, RampWave, NoiseWave, ConstantWave,
)

source = (
    SyntheticSource(
        pattern=SineWave(amplitude=10.0, period=timedelta(seconds=60), offset=20.0, phase=0.0),
        datastream="temperature",
        sample_rate=timedelta(seconds=1),
        duration=None,                  # None = until harness stops
    )
    .with_asset("pump-001")
)
```

Wave patterns:

| Pattern                                                                                          | Use case                                  |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------- |
| `SineWave(amplitude, period, offset=0.0, phase=0.0)`                                             | Cyclic signals (vibration, temperature)   |
| `SquareWave(amplitude, period, offset=0.0, duty_cycle=0.5)`                                      | On/off behavior                           |
| `RampWave(start=0.0, end=1.0, period=timedelta(seconds=60))`                                     | Monotonically increasing/decreasing signal |
| `NoiseWave(amplitude=1.0, offset=0.0, seed=None)`                                                | Random noise (use `seed=...` for determinism) |
| `ConstantWave(value=0.0)`                                                                        | Constant baseline                         |

Combine patterns by stacking sources on the same input — each source emits independently.

## `RandomSource`

Bounded fuzzing source. Always set `seed` in tests.

```python
from kelvin.testing.sources import RandomSource

source = (
    RandomSource(
        datastreams=["temperature", "pressure"],
        min_value=0.0,
        max_value=100.0,
        seed=42,                # MANDATORY for reproducibility
        count=100,              # None = unlimited
        value_type="number",    # or "boolean" / "string"
    )
    .with_asset("pump-001")
    .with_timing(timedelta(seconds=1))
)
```

## `DataFrameSource`

Replays a pandas DataFrame already in memory.

```python
import pandas as pd
from kelvin.testing.sources import DataFrameSource

df = pd.DataFrame({
    "timestamp":   pd.date_range("2024-01-01", periods=60, freq="1s"),
    "temperature": range(60),
})

source = (
    DataFrameSource(df, timestamp_column="timestamp")
    .with_asset("pump-001")
    .with_columns("temperature")
)
```

## `TabularSource`

Base class for row-based sources. Subclass it only when CSV / DataFrame don't fit (e.g., reading from Parquet or a SQL query). Implement `_rows()` to yield `(timestamp, asset, dict_of_values)` tuples.

## Common Pitfalls

- Calling `add_source` after `async with harness:` → `RuntimeError`. Always chain before connect.
- Forgetting `.with_asset(...)` and not providing `asset_column` in CSV → source emits nothing.
- Source datastream name not declared in the manifest → harness drops every message.
- `RandomSource` without `seed=` → flaky tests; always seed.
- Calling `run_until_idle(timeout=5)` when the source is configured for a 60-second window → only the first 5 seconds of data are emitted.
- Mixing `playback="realtime"` CSV with `with_timing(...)` — `with_timing` is ignored unless `ignore_timestamps=True`.
