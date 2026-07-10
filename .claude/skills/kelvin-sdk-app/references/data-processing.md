# Data Processing Reference

## When to Use

Use this file to implement tumbling, hopping, or rolling windows, handle DataFrame aggregations, and manage shared app state under concurrent handlers.

## Table of Contents
- [When to Use](#when-to-use)
- [Data Windows](#data-windows)
- [Tumbling Window](#tumbling-window)
- [Hopping Window](#hopping-window)
- [Rolling Window](#rolling-window)
- [State Management](#state-management)

## Data Windows

Expect window streams to return tuples: `(asset_name: str, df: pandas.DataFrame)`.

Apply these rules:
- Declare `inputs=[...]` explicitly.
- Use input stream names as DataFrame columns.
- Guard with `df.empty` before calculations.
- Handle NaN values explicitly (`pd.isna`, `pd.notna`).
- Use `window_start` when boundary alignment matters.

Select a window type by behavior:
- Tumbling: fixed, non-overlapping time windows.
- Hopping: fixed, overlapping time windows.
- Rolling: count-based sliding windows.

## Tumbling Window

```python
from datetime import datetime, timedelta
import pandas as pd

@app.task
async def process_tumbling() -> None:
    start = datetime.now().astimezone()

    async for asset, df in app.tumbling_window(
        window_size=timedelta(minutes=15),
        inputs=["casing_pressure", "tubing_pressure", "oil_rate", "gas_rate"],
    ).stream(window_start=start):
        if df.empty:
            continue

        avg_casing = float(df["casing_pressure"].mean())
        avg_oil = float(df["oil_rate"].mean())
        if pd.isna(avg_casing) or pd.isna(avg_oil):
            continue

        logger.info("Tumbling summary", asset=asset, avg_casing=avg_casing, avg_oil=avg_oil)
```

## Hopping Window

```python
from datetime import datetime, timedelta
import pandas as pd

@app.task
async def process_hopping() -> None:
    start = datetime.now().astimezone()

    async for asset, df in app.hopping_window(
        window_size=timedelta(minutes=30),
        hop_size=timedelta(minutes=10),
        inputs=["casing_pressure", "tubing_pressure", "temperature"],
    ).stream(window_start=start):
        if df.empty:
            continue

        diff = (df["casing_pressure"] - df["tubing_pressure"]).mean()
        max_temp = df["temperature"].max()
        if pd.isna(diff) or pd.isna(max_temp):
            continue

        logger.info("Hopping summary", asset=asset, diff_pressure=diff, max_temp=max_temp)
```

## Rolling Window

```python
import pandas as pd

@app.task
async def process_rolling() -> None:
    async for asset, df in app.rolling_window(
        count_size=20,
        slide=5,
        inputs=["oil_rate", "gas_rate"],
    ).stream():
        if df.empty:
            continue

        oil = float(df["oil_rate"].iloc[-1])
        gas = float(df["gas_rate"].iloc[-1])
        if pd.isna(oil) or pd.isna(gas):
            continue

        gor = gas / oil if oil > 0 else 0.0
        logger.info("Rolling summary", asset=asset, gor=gor)
```

## State Management

Handle concurrency:
- Stream handlers run concurrently.
- Protect shared mutable state with `asyncio.Lock`.

```python
import asyncio
from typing import Optional
from kelvin.application import AssetInfo
from kelvin.message import AssetDataMessage

state_lock = asyncio.Lock()
asset_state: dict[str, dict[str, int]] = {}

@app.stream(inputs=["oil_rate"])
async def update_state(msg: AssetDataMessage):
    async with state_lock:
        state = asset_state.setdefault(msg.resource.asset, {"count": 0})
        state["count"] += 1
```

Handle asset lifecycle:

```python
async def on_asset_change(new_asset: Optional[AssetInfo], old_asset: Optional[AssetInfo]):
    if new_asset is not None:
        asset_state.setdefault(new_asset.name, {"count": 0})
    elif old_asset is not None:
        asset_state.pop(old_asset.name, None)
```

Handle persistence:
- Use external storage for durable state.
- Use Timeseries API to seed startup state when needed.
