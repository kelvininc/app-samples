# Messages and Outputs Reference

## When to Use

Use this file to choose message classes to publish or handle (`Number`, `ControlChange`, `Recommendation`, `CustomAction`, evidences, data tags, parameters, and data quality messages).

## Table of Contents
- [Messages and Outputs Reference](#messages-and-outputs-reference)
  - [When to Use](#when-to-use)
  - [Table of Contents](#table-of-contents)
  - [Core Rules](#core-rules)
  - [Data Messages](#data-messages)
  - [Control Changes and Acks](#control-changes-and-acks)
  - [Custom Actions](#custom-actions)
  - [Recommendations](#recommendations)
  - [Recommendation Evidences](#recommendation-evidences)
  - [Data Tags](#data-tags)
    - [Publishing data tags](#publishing-data-tags)
    - [Consuming data tags](#consuming-data-tags)
  - [Asset Parameters](#asset-parameters)
  - [Data Quality Messages](#data-quality-messages)

## Core Rules

- Declare published `data_streams.outputs`, `control_changes.outputs`, and `custom_actions.outputs` in `app.yaml` before publishing.
- Use resource constructors that match the message type:
  - Data outputs: `KRNAssetDataStream(asset, stream)`
  - Recommendations, custom actions, data tags: `KRNAsset(asset)`
  - Parameter updates: `KRNAssetParameter(asset, parameter)`
- Embed control changes and custom actions in `Recommendation` unless explicitly asked to publish standalone.

For KRN constructors and parsing, use [krn.md](krn.md).
For declaration examples, use [app-yaml.md](app-yaml.md).

## Data Messages

```python
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number, String

await app.publish(Number(resource=KRNAssetDataStream("asset-1", "production_index"), payload=0.82))
await app.publish(String(resource=KRNAssetDataStream("asset-1", "optimization_status"), payload="stable"))
```

## Control Changes and Acks

Use control changes for set points and commands that require tracking and timeout handling.

```python
from datetime import timedelta
from kelvin.krn import KRNAssetDataStream
from kelvin.message import AssetDataMessage, ControlAck, ControlChange, StateEnum

await app.publish(
    ControlChange(
        resource=KRNAssetDataStream("asset-1", "choke_setpoint"),
        payload=75.0,
        expiration_date=timedelta(minutes=10),
        timeout=60,
        retries=3,
    )
)

async def on_control_change(msg: AssetDataMessage) -> None:
    ack = ControlAck(
        resource=msg.resource,
        state=StateEnum.applied,
        message="Control change successfully applied",
    )
    await app.publish(ack)
```

Send `ControlAck` only if the app receives control changes (`control_changes.inputs`).

## Custom Actions

Prefer including custom actions inside recommendations. Publish standalone only if explicitly required.

```python
from datetime import datetime, timedelta
from kelvin.krn import KRNAsset
from kelvin.message import CustomAction

standalone_action = CustomAction(
    resource=KRNAsset("asset-1"),
    type="email",
    title="Recommendation to reduce speed",
    description="It is recommended that the speed is reduced",
    expiration_date=datetime.now().astimezone() + timedelta(hours=1),
    payload={
        "recipient": "operations@example.com",
        "subject": "Recommendation to reduce speed",
        "body": "This is the email body",
    },
)
```

## Recommendations

Apply these guidelines:
- Include control changes/actions in the `Recommendation` payload.
- Add `kelvin_closed_loop` (boolean, default `false`) in asset parameters when recommendations include actions.
- Set `auto_accepted` based on `kelvin_closed_loop`.

```python
from datetime import datetime, timedelta
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message import ControlChange, CustomAction, Recommendation

recommendation = Recommendation(
    resource=KRNAsset("asset-1"),
    type="pressure_optimization",
    control_changes=[
        ControlChange(
            resource=KRNAssetDataStream("asset-1", "choke_setpoint"),
            payload=70.0,
            expiration_date=timedelta(minutes=10),
            timeout=60,
            retries=3,
        )
    ],
    actions=[
        CustomAction(
            resource=KRNAsset("asset-1"),
            type="work_order",
            title="Maintenance Required",
            description="Casing pressure exceeds threshold",
            expiration_date=datetime.now().astimezone() + timedelta(days=1),
            payload={"priority": "high", "equipment_id": "asset-1"},
        )
    ],
    expiration_date=timedelta(hours=1),
    auto_accepted=bool(app.assets["asset-1"].parameters.get("kelvin_closed_loop", False)),
)
await app.publish(recommendation)
```

## Recommendation Evidences

Prefer `Markdown` + `DataExplorer` as the default evidence pair.

Available evidence types:
- `Markdown`
- `DataExplorer`
- `LineChart`
- `BarChart`
- `Image`
- `IFrame`

```python
from datetime import datetime, timedelta
from kelvin.krn import KRNAssetDataStream
from kelvin.message.evidences import AggregationTypes, DataExplorer, DataExplorerSelector, Markdown

now = datetime.now().astimezone()

evidences = [
    Markdown(
        title="Pressure Summary",
        markdown="Detected sustained high casing pressure. Recommend lowering choke setpoint.",
    ),
    DataExplorer(
        title="Pressure Trend",
        start_time=now - timedelta(hours=6),
        end_time=now,
        selectors=[
            DataExplorerSelector(resource=KRNAssetDataStream("asset-1", "casing_pressure")),
            DataExplorerSelector(
                resource=KRNAssetDataStream("asset-1", "tubing_pressure"),
                agg=AggregationTypes.mean,
                time_bucket="5m",
            ),
        ],
    ),
]
```

## Data Tags

Use data tags for interval annotations. Declare consumed and produced tag names in `app.yaml` under `data_tags.inputs` and `data_tags.outputs`.

### Publishing data tags

`contexts` accepts only `KRNDatastream` instances, and each data stream must be declared as an input or output in the app's `app.yaml`.

```python
from datetime import datetime, timedelta
from kelvin.krn import KRNAsset, KRNDatastream
from kelvin.message import DataTag

await app.publish(
    DataTag(
        resource=KRNAsset("asset-1"),
        start_date=datetime.now().astimezone() - timedelta(hours=2),
        end_date=datetime.now().astimezone(),
        tag_name="cycle",
        description="Detected a well cycle",
        contexts=[KRNDatastream("temperature")],
    )
)
```

### Consuming data tags

Register `app.on_data_tag` to receive data tags declared in `data_tags.inputs`.

```python
from kelvin.message import DataTag

async def on_data_tag(tag: DataTag) -> None:
    logger.info("Received data tag", tag_name=tag.tag_name, resource=str(tag.resource))

app.on_data_tag = on_data_tag
```

## Asset Parameters

Use `AppParameters` to update one or more per-asset parameters.

```python
from kelvin.krn import KRNAssetParameter
from kelvin.message import AppParameter, AppParameters

await app.publish(
    AppParameters(
        parameters=[
            AppParameter(resource=KRNAssetParameter("asset-1", "max_casing_pressure"), value=1600.0),
            AppParameter(resource=KRNAssetParameter("asset-1", "kelvin_closed_loop"), value=True),
        ]
    )
)
```

## Data Quality Messages

Use `filters.is_data_quality_message` for quality streams.

```python
from kelvin.application import filters
from kelvin.krn import KRNAssetDataQuality, KRNAssetDataStreamDataQuality

async for msg in app.stream_filter(filters.is_data_quality_message):
    asset = msg.resource.asset
    quality = msg.resource.data_quality

    if isinstance(msg.resource, KRNAssetDataStreamDataQuality):
        stream = msg.resource.data_stream
        logger.info("Stream data quality", asset=asset, stream=stream, quality=quality, value=msg.payload)
    elif isinstance(msg.resource, KRNAssetDataQuality):
        logger.info("Asset data quality", asset=asset, quality=quality, value=msg.payload)
```
