# KRN (Kelvin Resource Name) Reference

## When to Use

Use this file to construct resource identifiers and parse KRN strings into typed objects.

## Table of Contents
- [When to Use](#when-to-use)
- [Overview](#overview)
- [Imports](#imports)
- [Common KRN Types](#common-krn-types)
- [Supported KRN Types](#supported-krn-types)
- [Parsing](#parsing)

## Overview

Use this pattern: `krn:<namespace_id>:<namespace_string>`.

## Imports

```python
from kelvin.krn import (
    KRN,
    KRNAsset,
    KRNAssetDataStream,
    KRNAssetParameter,
    KRNApp,
    KRNUser,
    KRNWorkloadAppVersion,
)
```

## Common KRN Types

**KRNAsset** - Reference an asset:
```python
krn = KRNAsset(asset="bp_16")
# str(krn) -> "krn:asset:bp_16"
# krn.asset -> "bp_16"
```

**KRNAssetDataStream** - Reference an asset/data stream pair:
```python
krn = KRNAssetDataStream(asset="bp_16", data_stream="motor_speed")
# str(krn) -> "krn:ad:bp_16/motor_speed"
# krn.asset -> "bp_16"
# krn.data_stream -> "motor_speed"
```

**KRNAssetParameter** - Reference an asset/parameter pair:
```python
krn = KRNAssetParameter(asset="bp_16", parameter="max_pressure")
# str(krn) -> "krn:ap:bp_16/max_pressure"
# krn.asset -> "bp_16"
# krn.parameter -> "max_pressure"
```

## Supported KRN Types

| Class | Namespace | Format | Constructor |
|-------|-----------|--------|-------------|
| `KRNAsset` | `asset` | `krn:asset:<asset>` | `KRNAsset(asset)` |
| `KRNAssetDataStream` | `ad` | `krn:ad:<asset>/<data_stream>` | `KRNAssetDataStream(asset, data_stream)` |
| `KRNAssetParameter` | `ap` | `krn:ap:<asset>/<parameter>` | `KRNAssetParameter(asset, parameter)` |
| `KRNParameter` | `param` | `krn:param:<parameter>` | `KRNParameter(parameter)` |
| `KRNApp` | `app` | `krn:app:<app>` | `KRNApp(app)` |
| `KRNAppVersion` | `appversion` | `krn:appversion:<app>/<version>` | `KRNAppVersion(app, version)` |
| `KRNUser` | `user` | `krn:user:<user>` | `KRNUser(user)` |
| `KRNServiceAccount` | `srv-acc` | `krn:srv-acc:<service_account>` | `KRNServiceAccount(service_account)` |
| `KRNDatastream` | `datastream` | `krn:datastream:<datastream>` | `KRNDatastream(datastream)` |
| `KRNWorkload` | `wl` | `krn:wl:<node>/<workload>` | `KRNWorkload(node, workload)` |
| `KRNWorkloadAppVersion` | `wlappv` | `krn:wlappv:<node>/<workload>:<app>/<version>` | `KRNWorkloadAppVersion(node, workload, app, version)` |
| `KRNRecommendation` | `recommendation` | `krn:recommendation:<recommendation_id>` | `KRNRecommendation(recommendation_id)` |
| `KRNJob` | `job` | `krn:job:<job>/<job_run_id>` | `KRNJob(job, job_run_id)` |
| `KRNSchedule` | `schedule` | `krn:schedule:<schedule>` | `KRNSchedule(schedule)` |
| `KRNAssetDataQuality` | `dqasset` | `krn:dqasset:<data_quality>:<asset>` | `KRNAssetDataQuality(asset, data_quality)` |
| `KRNAssetDataStreamDataQuality` | `dqad` | `krn:dqad:<data_quality>:<asset>/<data_stream>` | `KRNAssetDataStreamDataQuality(asset, data_stream, data_quality)` |

## Parsing

Parse KRN strings with `KRN.from_string(...)`:

```python
from kelvin.krn import KRN

krn = KRN.from_string("krn:asset:bp_16")
# Returns KRNAsset instance
# krn.asset -> "bp_16"

krn = KRN.from_string("krn:ad:bp_16/motor_speed")
# Returns KRNAssetDataStream instance
# krn.asset -> "bp_16"
# krn.data_stream -> "motor_speed"
```

`KRN.from_string(...)` returns a specific subclass for known namespaces and a base `KRN` for unknown namespaces.

Use these operations:
- `str(krn)` / `krn.encode()` - serialize to string
- `KRN.from_string(s)` - parse from string
- `KRN.validate(v)` - accepts KRN instance or string (Pydantic compatible)
- Equality (`==`) and hashing (usable as dict keys)
