# Kelvin API Client Reference

## When to Use

Use this file to implement reads/writes through `app.api` (apps, workloads, control changes, custom actions, recommendations, data tags, and timeseries queries).

## Table of Contents
- [When to Use](#when-to-use)
- [API Permissions](#api-permissions)
- [Payload Validation](#payload-validation)
- [Imports](#imports)
- [Request Failures](#request-failures)
- [Timeseries](#timeseries)
- [Control Changes](#control-changes)
- [Recommendations](#recommendations)
- [Apps](#apps)
- [App Parameters](#app-parameters)
- [App Workloads](#app-workloads)
- [Custom Actions](#custom-actions)
- [Data Tags](#data-tags)

## API Permissions

Every `app.api` call requires a corresponding permission in `app.yaml` under `api_permissions`. Format: `kelvin.permission.<domain>.<action>`.

To find the required permission for a specific API call, inspect the method's docstring in the SDK source under `kelvin.api.client.api`. Each method documents its **Permission Required** (e.g. `kelvin.permission.storage.read`). Look up the module matching the `app.api.<accessor>` being used (e.g. `app.api.timeseries` → `kelvin/api/client/api/timeseries.py`) and read the docstring of the method being called.

Example: if the app calls `app.api.timeseries.get_timeseries_range(...)` and `app.api.recommendation.list_recommendations(...)`, inspect those two methods, find their documented permissions, and add them:
```yaml
api_permissions:
  - kelvin.permission.storage.read
  - kelvin.permission.recommendation.read
```

## Payload Validation

All `app.api` request payloads and response types are defined as Pydantic models under `kelvin.api.client.model`. Before constructing a `data={}` dict or reading response fields:

1. Find the API method signature under `kelvin.api.client.api.<module>.py` to identify the `data` parameter type (e.g. `requests.ControlChangeCreate`).
2. Inspect that model class under `kelvin.api.client.model.requests` to verify exact field names, types, required vs optional, and defaults.
3. For response fields, inspect the return type under `kelvin.api.client.model.responses` or `kelvin.api.client.model.type`.
4. For enum-valued fields, inspect the allowed values under `kelvin.api.client.model.enum`.

Never guess or invent field names, enum values, or payload structures. Only use fields that exist in the model definitions.

## Imports

```python
from kelvin.api.base.data_model import AsyncKIterator, KList
from kelvin.api.base.error import APIError, ResponseError
from kelvin.api.client.model import enum, manifest, responses, type as type_
from kelvin.krn import KRN, KRNAssetDataStream, KRNAsset
```

## Request Failures

Best practice is to wrap requests in `try/except` and handle API client exceptions.

```python
logger = logging.getLogger(__name__)

try:
    recs = await app.api.recommendation.list_recommendations(
        data={"resources": ["krn:asset:bp_16"]},
        sort_by=["created"],  
        direction="desc",
    )
except (APIError, ResponseError) as exc:
    # API-level failure or unexpected API response payload/shape
    logger.error("Unexpected recommendation response: %s", exc)
except Exception as exc:
    # Network/runtime fallback
    logger.exception("Unexpected request failure: %s", exc)
else:
    if recs:
        first = recs[0]
        logger.info("Fetched recommendations, first_id=%s", first.id)
```

## Timeseries

### Get Last Point

```python
last: list[responses.TimeseriesLastGet] = await app.api.timeseries.get_timeseries_last(
    data={"selectors": [{"resource": "krn:ad:bp_02/motor_speed"}]}
)

last_point: responses.TimeseriesLastGet = last[0]

# Fields
resource: KRNAssetDataStream = last_point.resource   # krn:ad:bp_02/motor_speed
source: KRN | None = last_point.source               # krn:wlappv:my-cluster/bp-opcua-bridge:kelvin-bridge-opcua-client/3.3.3
data_type: str | None = last_point.data_type         # data;pt=number
last_value: int | float | str | bool | dict[str, Any] | None = last_point.last_value  # 1945.2321735985631
last_timestamp: datetime | None = last_point.last_timestamp  # 2023-12-19 22:48:09.129360+00:00
```

### Get Range

```python
series: AsyncKIterator[responses.TimeseriesRangeGet] = await app.api.timeseries.get_timeseries_range(
    data={
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-02T00:00:00Z",
        "selectors": [{"resource": "krn:ad:pcp_01/casing_pressure"}],
        "agg": "mean", 
        "time_bucket": "1h",
        "fill": "none",
        "order": "ASC",
        "group_by_selector": True,
    }
)

# Fields
async for point in series:
    resource: KRNAssetDataStream | None = point.resource   # krn:ad:pcp_01/casing_pressure
    timestamp: datetime | None = point.timestamp           # 2026-01-01 00:00:00+00:00
    payload: int | float | str | bool | dict[str, Any] | None = point.payload  # 59.933886464416695
```

DataFrame conversion pattern:

```python
result = await app.api.timeseries.get_timeseries_range(
    data={
        "start_time": datetime.now().astimezone() - timedelta(days=7),
        "end_time": datetime.now().astimezone(),
        "agg": "mean",
        "time_bucket": "30s",
        "selectors": [
            {"resource": f"krn:ad:{asset_name}/gas_rate"},
            {"resource": f"krn:ad:{asset_name}/casing_pressure"},
        ],
        "order": "ASC",
        "fill": "none",
    }
)

df = result.to_df(datastreams_as_column=True)
df.head(5)
#     timestamp                         asset_name   gas_rate     casing_pressure
# 0   2025-12-02 12:04:43.811316+00:00  asset-1     424.623553   NaN
# 1   2025-12-02 12:04:58.839218+00:00  asset-1     NaN          28.282359
```

For window-first real-time patterns, use [data-processing.md](data-processing.md).

## Control Changes

### Get Last Control Change

```python
# sort_by: id, resource, last_state, created, updated, timestamp
last_cc: list[responses.ControlChangeGet] = await app.api.control_change.get_control_change_last(
    data={"resources": ["krn:ad:pcp_03/speed_sp"], "states": ["applied", "sent"]},
    sort_by=["timestamp"],
    direction="desc",
)

cc: responses.ControlChangeGet = last_cc[0]

# Fields
id_: UUID = cc.id                                  # d16ce3c3-1082-411b-b04b-38034839d866
resource: KRNAssetDataStream = cc.resource         # krn:ad:pcp_03/speed_sp
payload: int | float | str | bool | dict[str, Any] | None = cc.payload  # 18.000000000000004
last_state: enum.ControlChangeState | None = cc.last_state  # applied
timestamp: datetime | None = cc.timestamp          # 2025-07-02 19:30:55.221910+00:00
```

### Get Control Change Range

```python
# sort_by: id, resource, last_state, created, updated, timestamp
cc_range: list[responses.ControlChangeGet] = await app.api.control_change.get_control_change_range(
    data={
        "start_date": "2026-01-01T00:00:00Z",
        "end_date": "2026-01-31T00:00:00Z",
        "resources": ["krn:ad:pcp_01/speed_sp"],
    },
    sort_by=["timestamp"],
    direction="desc",
)

cc: responses.ControlChangeGet = cc_range[0]

# Fields
id_: UUID = cc.id                                  # 041eb8ce-eabb-418f-99ed-aded6f1b2e1e
resource: KRNAssetDataStream = cc.resource         # krn:ad:pcp_01/speed_sp
payload: int | float | str | bool | dict[str, Any] | None = cc.payload  # 49.5
last_state: enum.ControlChangeState | None = cc.last_state  # applied
timestamp: datetime | None = cc.timestamp          # 2026-01-29 19:19:18.083917+00:00
```

### List Control Changes

```python
# sort_by: id, resource, last_state, created, updated, timestamp
control_changes: list[responses.ControlChangeGet] = await app.api.control_change.list_control_changes(
    data={"resources": ["krn:ad:pcp_01/speed_sp"], "states": ["sent", "applied"]},
    sort_by=["timestamp"],
    direction="desc",
)

cc: responses.ControlChangeGet = control_changes[0]

# Fields
id_: UUID = cc.id                                  # b907e8a0-61ea-4203-942c-53e40000fba3
resource: KRNAssetDataStream = cc.resource         # krn:ad:pcp_01/speed_sp
payload: int | float | str | bool | dict[str, Any] | None = cc.payload  # 70
created_by: str | None = cc.created_by             # example@kelvininc.com
last_state: enum.ControlChangeState | None = cc.last_state  # applied
```

### Get Control Change

```python
control_change: responses.ControlChangeGet = await app.api.control_change.get_control_change(
    control_change_id="b907e8a0-61ea-4203-942c-53e40000fba3"
)

# Fields
id_: UUID = control_change.id                           # b907e8a0-61ea-4203-942c-53e40000fba3
resource: KRNAssetDataStream = control_change.resource  # krn:ad:pcp_01/speed_sp
payload: int | float | str | bool | dict[str, Any] | None = control_change.payload  # 70
last_state: enum.ControlChangeState | None = control_change.last_state  # applied
trace_id: str | None = control_change.trace_id     # b907e8a0-61ea-4203-942c-53e40000fba3
```

## Recommendations

### Get Last Recommendation

```python
# sort_by: id, custom_identifier, resource, description, confidence, expiration_date, source, state, type, created, updated, updated_by
last_recs: list[type_.Recommendation] = await app.api.recommendation.get_recommendation_last(
    data={"resources": ["krn:asset:pcp_01"], "states": ["pending", "accepted"]},
    sort_by=["created"],
    direction="desc",
)

rec: type_.Recommendation = last_recs[0]

# Fields
id_: UUID | None = rec.id                          # 019cad62-f803-7cbb-8c59-a4bc7075cd08
rec_type: str = rec.type                           # no_action
type_title: str | None = rec.type_title            # No Action
resource: KRNAsset = rec.resource                  # krn:asset:pcp_01
description: str | None = rec.description          # Above max Drawdown, parameters stable
state: enum.RecommendationState | None = rec.state # pending
```

### List Recommendations

```python
# sort_by: id, custom_identifier, resource, description, confidence, expiration_date, source, state, type, created, updated, updated_by
recs: list[type_.Recommendation] = await app.api.recommendation.list_recommendations(
    data={"resources": ["krn:asset:pcp_01"], "states": ["pending", "accepted"]},
    sort_by=["created"],
    direction="desc",
)

rec: type_.Recommendation = recs[0]

# Fields
id_: UUID | None = rec.id                          # 019cad62-f803-7cbb-8c59-a4bc7075cd08
rec_type: str = rec.type                           # no_action
type_title: str | None = rec.type_title            # No Action
resource: KRNAsset = rec.resource                  # krn:asset:pcp_01
state: enum.RecommendationState | None = rec.state # pending
created: datetime | None = rec.created             # 2026-03-02 07:11:10.344599+00:00
```

### Get Recommendation Range

```python
# sort_by: id, custom_identifier, resource, description, confidence, expiration_date, source, state, type, created, updated, updated_by
rec_range: list[type_.Recommendation] = await app.api.recommendation.get_recommendation_range(
    data={
        "start_date": "2026-01-01T00:00:00Z",
        "end_date": "2026-01-31T00:00:00Z",
        "resources": ["krn:asset:pcp_01"],
    },
    sort_by=["created"],
    direction="desc",
)

rec: type_.Recommendation = rec_range[0]

# Fields
id_: UUID | None = rec.id                          # 019c1048-cbac-7068-aec1-ae41290d4842
rec_type: str = rec.type                           # no_action
resource: KRNAsset = rec.resource                  # krn:asset:pcp_01
description: str | None = rec.description          # No action - monitoring
state: enum.RecommendationState | None = rec.state # expired
expiration_date: datetime | None = rec.expiration_date  # 2026-01-31 03:02:10.741612+00:00
```

### Get Recommendation

```python
rec: responses.RecommendationGet = await app.api.recommendation.get_recommendation(
    recommendation_id="019c1048-cbac-7068-aec1-ae41290d4842"
)

# Fields
id_: UUID | None = rec.id                          # 019c1048-cbac-7068-aec1-ae41290d4842
rec_type: str = rec.type                           # no_action
resource: KRNAsset = rec.resource                  # krn:asset:pcp_01
state: enum.RecommendationState | None = rec.state # expired
updated_by: KRN | None = rec.updated_by            # krn:job:expiration-worker/1771428233846
```

## Apps

### List Apps

```python
# sort_by: name, title, type, latest_version, created_at, created_by, updated_at, updated_by
apps: list[type_.AppShort] = await app.api.apps.list_apps(
    app_types=["app"],
    sort_by=["updated_at"],
    direction="desc",
)

app_short: type_.AppShort = apps[0]

# Fields
name: str = app_short.name                         # esp-optimization
title: str = app_short.title                       # ESP Optimization
app_type: enum.AppType = app_short.type            # app
latest_version: str | None = app_short.latest_version  # 1.0.06112307
status: enum.AppStatus | None = app_short.status   # running
```

### Get App

```python
app_obj: responses.AppGet = await app.api.apps.get_app(app_name="esp-optimization")

# Fields
name: str = app_obj.name                           # esp-optimization
title: str = app_obj.title                         # ESP Optimization
latest_version: str | None = app_obj.latest_version  # 1.0.06112307
deployment: responses.Deployment | None = app_obj.deployment  # status='running' resource_count=ResourceCount(total=66, running=66)
updated_by: KRN | None = app_obj.updated_by        # krn:user:example@kelvininc.com
```

### Get App Version

```python
app_version: responses.AppVersionGet = await app.api.apps.get_app_version(
    app_name="esp-optimization",
    app_version="1.0.06112307",
)

# Fields
name: str = app_version.name                       # esp-optimization
version: str = app_version.version                 # 1.0.06112307
flags: manifest.Flags | None = app_version.flags   # spec_version='5.0.0' legacy_data_types=False legacy_extras=None gateway_mode=None enable_runtime_update=None deployment=None resources_required=True
io_first: manifest.AppIO | None = app_version.io[0] if app_version.io else None  # AppIO(name='casing_pressure', data_type='number', way='input', storage='node-and-cloud', unit=None)
param_first: manifest.Parameter | None = app_version.parameters[0] if app_version.parameters else None  # Parameter(name='kelvin_control_mode', title='kelvin_control_mode', data_type='string', default='Open')
```

### List App Context

```python
# sort_by: resource, source
contexts: list[type_.AppsResourceContext] = await app.api.apps.list_apps_context(
    data={"resources": ["krn:asset:pcp_01"]},
    sort_by=["resource"],
    direction="asc",
)

context: type_.AppsResourceContext = contexts[0]

# Fields
resource: KRNAssetDataStream | None = context.resource  # krn:ad:pcp_01/casing_pressure
source: KRN | None = context.source                     # krn:wlappv:my-cluster/pcp-opcua:kelvin-bridge-opcua-client/3.4.7
```

## App Parameters

### List App Parameters

```python
# sort_by: name, title, app_name, data_type, created_at, created_by, updated_at, updated_by
params: list[type_.AppParameter] = await app.api.app_parameters.list_app_parameters(
    data={"names": ["kelvin_closed_loop"]},
    sort_by=["name"],
    direction="asc",
)

param: type_.AppParameter = params[0]

# Fields
app_name: str | None = param.app_name              # compressor-monitor
name: str | None = param.name                      # kelvin_closed_loop
data_type: enum.ParameterType | None = param.data_type  # boolean
updated_at: datetime | None = param.updated_at     # 2026-02-05 04:58:54.133504+00:00
```

## App Workloads

### List Workloads

```python
# sort_by: name, title, app_name, app_version, cluster_name, node_name, status_last_seen, status_state, created_at, created_by, updated_at, updated_by
workloads: list[type_.WorkloadSummary] = await app.api.app_workloads.list_workloads(
    app_names=["esp-optimization"],
    statuses=["running"],
    sort_by=["updated_at"],
    direction="desc",
)

workload_summary: type_.WorkloadSummary = workloads[0]

# Fields
name: str | None = workload_summary.name           # esp-optimization-dgocvxnoqdgm0
app_name: str | None = workload_summary.app_name   # esp-optimization
app_version: str | None = workload_summary.app_version  # 1.0.06112307
download_status: enum.WorkloadDownloadStatus | None = workload_summary.download_status  # pending
status: type_.WorkloadStatus | None = workload_summary.status  # last_seen=datetime.datetime(2026, 3, 2, 11, 5, 47, 482840, tzinfo=TzInfo(UTC)) message='Running' state='running' warnings=None
```

### Get Workload

```python
workload: responses.WorkloadGet = await app.api.app_workloads.get_workload(
    workload_name="esp-optimization-dgocvxnoqdgm0"
)

# Fields
name: str | None = workload.name                   # esp-optimization-dgocvxnoqdgm0
app_name: str | None = workload.app_name           # esp-optimization
app_version: str | None = workload.app_version     # 1.0.06112307
cluster_name: str | None = workload.cluster_name   # my-cluster
node_name: str | None = workload.node_name         # my-cluster
download_status: enum.WorkloadDownloadStatus | None = workload.download_status  # pending
status: type_.WorkloadStatus | None = workload.status  # last_seen=datetime.datetime(2026, 3, 2, 11, 5, 58, 380615, tzinfo=TzInfo(UTC)) message='Running' state='running' warnings=None
```

## Custom Actions

### List Custom Actions

```python
# sort_by: id, title, resource, description, expiration_date, timestamp, trace_id, type, state, created, created_by, updated, updated_by
actions: list[type_.CustomAction] = await app.api.custom_actions.list_custom_actions(
    data={"states": ["pending", "completed"]},
    sort_by=["created"],
    direction="desc",
)

action: type_.CustomAction = actions[0]

# Fields
id_: UUID = action.id                              # c007e19d-3a71-406b-aaa1-372c58a85760
action_type: str = action.type                     # Slack Message
resource: KRNAsset = action.resource               # krn:asset:pcp_01
state: enum.CustomActionState = action.state       # completed
message: str | None = action.message               # Slack message sent
```

### Get Custom Action

```python
action: responses.CustomActionGet = await app.api.custom_actions.get_custom_action(
    action_id="c007e19d-3a71-406b-aaa1-372c58a85760"
)

# Fields
id_: UUID = action.id                              # c007e19d-3a71-406b-aaa1-372c58a85760
action_type: str = action.type                     # Slack Message
payload: dict[str, Any] | None = action.payload    # {'channel': 'sales-demo-notifications', 'message': {'text': 'Torque issues detected on well pcp_01'}}
state: enum.CustomActionState = action.state       # completed
updated_by: KRN = action.updated_by                # krn:wl:my-cluster/slack-message-sender
```

## Data Tags

### List Data Tags

```python
# sort_by: id, start_date, end_date, tag_name, resource, source, description, created, updated
tags: list[type_.DataTag] = await app.api.data_tag.list_data_tag(
    data={"start_date": "2026-01-01T00:00:00Z", "end_date": "2026-01-31T00:00:00Z"},
    sort_by=["start_date"],
    direction="desc",
)

tag: type_.DataTag = tags[0]

# Fields
id_: UUID | None = tag.id                          # a4df8bb6-001d-48b0-8d6c-5b84ca8e5e46
tag_name: str | None = tag.tag_name                # Plunger Arrival
resource: KRNAsset | None = tag.resource           # krn:asset:b6b16470-b95a-49e6-8aa0-879bf0b1a0c4
start_date: datetime | None = tag.start_date       # 2026-01-27 21:09:20.184000+00:00
end_date: datetime | None = tag.end_date           # 2026-01-29 17:02:27.395000+00:00
```

### Get Data Tag


```python
tag: responses.DataTagGet = await app.api.data_tag.get_data_tag(
    datatag_id="a4df8bb6-001d-48b0-8d6c-5b84ca8e5e46"
)

# Fields
id_: UUID | None = tag.id                          # a4df8bb6-001d-48b0-8d6c-5b84ca8e5e46
tag_name: str | None = tag.tag_name                # Plunger Arrival
resource: KRNAsset | None = tag.resource           # krn:asset:b6b16470-b95a-49e6-8aa0-879bf0b1a0c4
source: KRN | None = tag.source                    # krn:user:example@kelvininc.com
contexts: list[KRN] | None = tag.contexts          # []
```
