# Importer Applications Reference

## When to Use

Use this file when building or reviewing a Kelvin importer application (`type: importer`) that ingests data from an external system and publishes it to the Kelvin Platform.

Typical signals:
- The app is described as a connector, importer, or ingestion service.
- `app.yaml` uses `type: importer`.
- The app reads from MQTT, RTSP, OPC-UA, REST, files, databases, or another external source.
- The app needs runtime IO mapping in Kelvin UI instead of statically declared `data_streams.inputs`.

## Table of Contents
- [Importer Applications Reference](#importer-applications-reference)
  - [When to Use](#when-to-use)
  - [Table of Contents](#table-of-contents)
  - [How Importers Differ from SmartApps](#how-importers-differ-from-smartapps)
  - [app.yaml Structure](#appyaml-structure)
  - [importer\_io Capabilities](#importer_io-capabilities)
  - [UI Schemas](#ui-schemas)
    - [configuration.json](#configurationjson)
    - [io\_default.json](#io_defaultjson)
  - [main.py Runtime Pattern](#mainpy-runtime-pattern)
  - [OPC-UA Connector Pattern](#opc-ua-connector-pattern)
    - [app.yaml](#appyaml)
    - [configuration.json](#configurationjson-1)
    - [io\_default.json](#io_defaultjson-1)
    - [main.py](#mainpy)
  - [Publishing Data](#publishing-data)
  - [Custom Actions](#custom-actions)
  - [requirements.txt and Local Config](#requirementstxt-and-local-config)
  - [Checklist and Pitfalls](#checklist-and-pitfalls)

## How Importers Differ from SmartApps

| Area | SmartApp (`type: app`) | Importer (`type: importer`) |
|---|---|---|
| Data declaration | `data_streams.inputs/outputs` | `importer_io` |
| Runtime pattern | `@app.stream()`, `@app.timer()`, `@app.task` | Manual async loop with `await app.connect()` |
| Mapping model | Static stream names in `app.yaml` | Operator-configured stream mappings in Kelvin UI |
| Per-stream UI | Usually `parameters.json` | `io_configuration` schema keyed by importer profile |
| Control changes | `control_changes.inputs/outputs` + `on_control_change` | `importer_io[].control: true` + `on_control_change` |
| Custom actions | Top-level `custom_actions.inputs/outputs` + `on_custom_action` | Top-level `custom_actions.inputs/outputs` + `on_custom_action` (same schema as SmartApps) |
| Primary purpose | React to Kelvin platform data | Ingest from an external source and publish to Kelvin |

Do not structure an importer like a SmartApp:
- Do not add `data_streams.inputs` for external topics or signals.
- Do not rely on `@app.stream()` as the primary ingestion mechanism.
- Do not assume asset-to-stream mapping is fixed in `app.yaml`.

## app.yaml Structure

Use `type: importer` and declare importer capabilities with `importer_io`.

```yaml
spec_version: 5.0.0
type: importer

name: mqtt-connector
title: MQTT Connector
description: Ingests data from an MQTT broker and publishes it to the Kelvin Platform.
version: 1.0.0

flags:
  enable_runtime_update:
    configuration: true

importer_io:
  - name: default
    data_types:
      - number
      - string
      - boolean
      - object
    control: false

ui_schemas:
  configuration: ui_schemas/configuration.json
  io_configuration:
    default: ui_schemas/io_default.json

defaults:
  configuration:
    mqtt:
      host: <% secrets.mqtt-host %>
      port: 1883
      client_id: kelvin-mqtt-connector
    reconnect_interval: 5
  system:
    resources:
      requests:
        cpu: 100m
        memory: 128Mi
      limits:
        cpu: 200m
        memory: 256Mi
```

Structure rules:
- `importer_io` replaces static `data_streams` declarations.
- `ui_schemas.io_configuration.<profile>` must match `importer_io[].name`.
- Use `defaults.configuration` for connector settings such as host, port, polling interval, API endpoints, and authentication credentials.
- When the importer uses `app.api`, add `api_permissions` at the root level. See [api-client.md — API Permissions](../kelvin-sdk/references/api-client.md#api-permissions).

## importer_io Capabilities

`importer_io` declares what kinds of streams the importer can publish or control, not the final deployed mapping.

```yaml
importer_io:
  - name: default
    data_types:
      - number
      - string
      - boolean
      - object
    control: true
```

Rules:
- Use one profile when all mapped streams share the same configuration shape.
- Add multiple profiles only when different stream classes need different per-stream configuration.
- Set `control: true` only if the importer handles incoming control changes.
- Treat actual asset and stream mapping as runtime configuration owned by the operator in Kelvin UI.

## UI Schemas

Importers usually need two schema layers:
- `configuration.json` for app-level connector settings.
- `io_default.json` for per-stream mapping settings used when an operator binds external data to Kelvin assets.

### configuration.json

```json
{
  "type": "object",
  "title": "MQTT Connector Configuration",
  "properties": {
    "mqtt": {
      "type": "object",
      "title": "MQTT Broker",
      "properties": {
        "host": {
          "type": "string",
          "title": "Broker Host"
        },
        "port": {
          "type": "number",
          "title": "Broker Port",
          "default": 1883,
          "minimum": 1,
          "maximum": 65535
        },
        "client_id": {
          "type": "string",
          "title": "Client ID",
          "default": "kelvin-mqtt-connector"
        }
      },
      "required": ["host", "port"]
    },
    "reconnect_interval": {
      "type": "number",
      "title": "Reconnect Interval (seconds)",
      "default": 5,
      "minimum": 1,
      "maximum": 300
    }
  },
  "required": ["mqtt"]
}
```

### io_default.json

```json
{
  "type": "object",
  "title": "Stream IO Configuration",
  "properties": {
    "topic": {
      "type": "string",
      "title": "MQTT Topic Filter"
    },
    "primitive": {
      "type": "string",
      "title": "Data Type",
      "enum": ["number", "string", "boolean", "object"],
      "default": "number"
    }
  },
  "required": ["topic", "primitive"]
}
```

Runtime access pattern:

```python
topic_filter = stream_ds.configuration.get("topic")
primitive = stream_ds.configuration.get("primitive", "number")
```

## main.py Runtime Pattern

Importers own the event loop. Use `await app.connect()` and drive reconnection, external polling, or message consumption manually.

```python
import asyncio
import json

import aiomqtt
from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import KMessageTypeData, Message

app = KelvinApp()


def parse_payload(raw: bytes, primitive: str) -> object:
    text = raw.decode("utf-8").strip()
    if primitive == "number":
        return float(text)
    if primitive == "boolean":
        return text.lower() in ("true", "1", "yes")
    if primitive == "object":
        return json.loads(text)
    return text


def build_topic_map() -> dict[str, list[tuple[str, str, str]]]:
    topic_map: dict[str, list[tuple[str, str, str]]] = {}
    for asset_name, asset_info in app.assets.items():
        for stream_name, stream_ds in asset_info.datastreams.items():
            topic_filter = stream_ds.configuration.get("topic")
            if not topic_filter:
                logger.warning("Missing topic mapping", asset=asset_name, stream=stream_name)
                continue
            primitive = stream_ds.configuration.get("primitive", "number")
            topic_map.setdefault(topic_filter, []).append((asset_name, stream_name, primitive))
    return topic_map


async def main() -> None:
    await app.connect()

    while True:
        config = app.app_configuration
        mqtt_cfg = config.get("mqtt", {})
        host = mqtt_cfg.get("host", "localhost")
        port = int(mqtt_cfg.get("port", 1883))
        client_id = mqtt_cfg.get("client_id", "kelvin-mqtt-connector")
        reconnect_interval = int(config.get("reconnect_interval", 5))

        try:
            async with aiomqtt.Client(
                hostname=host,
                port=port,
                identifier=client_id,
            ) as client:
                topic_map = build_topic_map()
                for topic_filter in topic_map:
                    await client.subscribe(topic_filter)

                async for mqtt_msg in client.messages:
                    targets = [
                        (asset, stream, primitive)
                        for topic_filter, mappings in topic_map.items()
                        for asset, stream, primitive in mappings
                        if mqtt_msg.topic.matches(topic_filter)
                    ]

                    for asset, stream, primitive in targets:
                        payload = parse_payload(mqtt_msg.payload, primitive)
                        await app.publish(
                            Message(
                                type=KMessageTypeData(primitive=primitive),
                                resource=KRNAssetDataStream(asset, stream),
                                payload=payload,
                            )
                        )
        except (aiomqtt.MqttError, OSError) as exc:
            logger.warning("Connector reconnecting", error=str(exc), wait_seconds=reconnect_interval)
            await asyncio.sleep(reconnect_interval)


if __name__ == "__main__":
    asyncio.run(main())
```

Runtime rules:
- Use `await app.connect()` rather than `app.run()` when the importer owns the async loop.
- Re-read `app.app_configuration` inside the reconnect or polling loop so runtime updates take effect.
- Build routing from `app.assets` and per-stream `stream_ds.configuration`.
- Catch both SDK/client exceptions and `OSError` for network failures.
- Use `msg.topic.matches(...)` or the equivalent client-side wildcard matcher instead of manual topic matching.

If the importer also receives platform control changes, wire `app.on_control_change` explicitly and handle acknowledgements there.

## OPC-UA Connector Pattern

Use the same importer structure, but replace topic-based mapping with OPC-UA node configuration per mapped stream.

### app.yaml

```yaml
spec_version: 5.0.0
type: importer

name: opcua-connector
title: OPC-UA Connector
description: Reads values from an OPC-UA server and publishes them to the Kelvin Platform.
version: 1.0.0

flags:
  enable_runtime_update:
    configuration: true

importer_io:
  - name: default
    data_types:
      - number
      - string
      - boolean
      - object
    control: false

ui_schemas:
  configuration: ui_schemas/configuration.json
  io_configuration:
    default: ui_schemas/io_default.json

defaults:
  configuration:
    opcua:
      url: opc.tcp://opcua.example.internal:4840
      namespace: 2
      security_mode: none
    poll_interval: 5
  system:
    resources:
      requests:
        cpu: 100m
        memory: 128Mi
      limits:
        cpu: 300m
        memory: 256Mi
```

Notes:
- Put authentication fields (certificates, usernames, passwords) in `defaults.configuration` and expose them through `ui_schemas/configuration.json`.
- Keep per-node mapping in `io_default.json`, not in `defaults.configuration`.

### configuration.json

```json
{
  "type": "object",
  "title": "OPC-UA Connector Configuration",
  "properties": {
    "opcua": {
      "type": "object",
      "title": "OPC-UA Server",
      "properties": {
        "url": {
          "type": "string",
          "title": "Server URL",
          "description": "OPC-UA endpoint, for example opc.tcp://host:4840"
        },
        "namespace": {
          "type": "number",
          "title": "Default Namespace Index",
          "default": 2,
          "minimum": 0
        },
        "security_mode": {
          "type": "string",
          "title": "Security Mode",
          "enum": ["none", "sign", "sign_and_encrypt"],
          "default": "none"
        }
      },
      "required": ["url"]
    },
    "poll_interval": {
      "type": "number",
      "title": "Polling Interval (seconds)",
      "default": 5,
      "minimum": 1,
      "maximum": 300
    }
  },
  "required": ["opcua"]
}
```

### io_default.json

```json
{
  "type": "object",
  "title": "OPC-UA Stream IO Configuration",
  "properties": {
    "node_id": {
      "type": "string",
      "title": "Node ID",
      "description": "Full OPC-UA node id, for example ns=2;s=Machine.Temperature"
    },
    "primitive": {
      "type": "string",
      "title": "Data Type",
      "enum": ["number", "string", "boolean", "object"],
      "default": "number"
    }
  },
  "required": ["node_id", "primitive"]
}
```

### main.py

```python
import asyncio
import json
from typing import Any

from asyncua import Client
from asyncua.ua.uaerrors import UaError
from kelvin.application import KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import KMessageTypeData, Message

app = KelvinApp()


def normalize_value(value: Any, primitive: str) -> Any:
    if primitive == "number":
        return float(value)
    if primitive == "boolean":
        return bool(value)
    if primitive == "object":
        if isinstance(value, (dict, list)):
            return value
        return json.loads(json.dumps(value, default=str))
    return str(value)


def build_node_map() -> dict[str, list[tuple[str, str, str]]]:
    node_map: dict[str, list[tuple[str, str, str]]] = {}
    for asset_name, asset_info in app.assets.items():
        for stream_name, stream_ds in asset_info.datastreams.items():
            node_id = stream_ds.configuration.get("node_id")
            if not node_id:
                logger.warning("Missing OPC-UA node mapping", asset=asset_name, stream=stream_name)
                continue
            primitive = stream_ds.configuration.get("primitive", "number")
            node_map.setdefault(node_id, []).append((asset_name, stream_name, primitive))
    return node_map


async def main() -> None:
    await app.connect()

    while True:
        config = app.app_configuration
        opcua_cfg = config.get("opcua", {})
        url = opcua_cfg.get("url")
        poll_interval = int(config.get("poll_interval", 5))

        if not url:
            logger.warning("Missing OPC-UA server URL", wait_seconds=poll_interval)
            await asyncio.sleep(poll_interval)
            continue

        node_map = build_node_map()
        if not node_map:
            logger.warning("No OPC-UA node mappings configured", wait_seconds=poll_interval)
            await asyncio.sleep(poll_interval)
            continue

        try:
            async with Client(url=url) as client:
                logger.info("Connected to OPC-UA server", url=url, mapped_nodes=len(node_map))

                while True:
                    config = app.app_configuration
                    poll_interval = int(config.get("poll_interval", 5))
                    node_map = build_node_map()

                    for node_id, targets in node_map.items():
                        node = client.get_node(node_id)
                        raw_value = await node.read_value()

                        for asset, stream, primitive in targets:
                            payload = normalize_value(raw_value, primitive)
                            await app.publish(
                                Message(
                                    type=KMessageTypeData(primitive=primitive),
                                    resource=KRNAssetDataStream(asset, stream),
                                    payload=payload,
                                )
                            )

                    await asyncio.sleep(poll_interval)
        except (UaError, OSError, ValueError, TypeError) as exc:
            logger.warning("OPC-UA polling failed, reconnecting", error=str(exc), wait_seconds=poll_interval)
            await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
```

OPC-UA-specific guidance:
- Read per-stream node mapping from `stream_ds.configuration["node_id"]`.
- Polling is the simplest baseline pattern; subscription-based updates can be added later if required.
- Rebuild the node map inside the loop when runtime mapping changes must take effect without redeploy.
- Catch `UaError` and `OSError` so transport, browse, and read failures do not crash the importer.
- Normalize complex values before publishing when the target Kelvin primitive is `object`.

## Publishing Data

Publish Kelvin data with `Message` and `KMessageTypeData` using the mapped asset and stream name.

```python
await app.publish(
    Message(
        type=KMessageTypeData(primitive="number"),
        resource=KRNAssetDataStream(asset, "temperature"),
        payload=98.6,
    )
)
```

Guidance:
- Match the `primitive` to the mapped stream data type.
- Use `object` for JSON-like payloads.
- Publish only to mapped asset/stream targets derived from the runtime IO configuration.

## Custom Actions

Importers can receive custom actions from the Kelvin Platform (for example, to trigger a remote command on the external system) and publish result actions back. The schema is identical to SmartApps: declare a top-level `custom_actions:` block in `app.yaml` (outside `importer_io`) and wire the `on_custom_action` callback before `await app.connect()`.

```yaml
spec_version: 5.0.0
type: importer

name: opcua-connector
# ... importer_io, ui_schemas, defaults, etc.

custom_actions:
  inputs:
    - type: restart_device
    - type: write_node
  outputs:
    - type: device_status
```

Rules:
- `custom_actions` lives at the top level of `app.yaml`, NOT inside `importer_io`.
- Each entry under `inputs` / `outputs` is a `type:` string that matches the `type` field on incoming/outgoing `CustomAction` / `CustomActionResult` messages.
- Only declare `inputs` if the importer handles incoming actions; only declare `outputs` if it publishes them.
- Acknowledge every input action with a `CustomActionResult` so the platform tracks completion.

```python
import asyncio

from kelvin.application import KelvinApp
from kelvin.message import CustomAction, CustomActionResult

app = KelvinApp()


async def on_action(msg: CustomAction) -> None:
    try:
        if msg.type == "restart_device":
            await restart_device(msg.payload)  # connector-specific
            success, message = True, "device restarted"
        elif msg.type == "write_node":
            await write_node(msg.payload)
            success, message = True, "node written"
        else:
            success, message = False, f"unknown action type: {msg.type}"
    except Exception as exc:  # noqa: BLE001 - acknowledge failures explicitly
        success, message = False, str(exc)

    await app.publish(
        CustomActionResult(
            resource=msg.resource,
            payload={"id": str(msg.id), "success": success, "message": message},
        )
    )


async def main() -> None:
    app.on_custom_action = on_action
    await app.connect()
    # ... ingestion loop
```

Custom-action notes:
- Assign `app.on_custom_action` BEFORE `await app.connect()`; callbacks registered after connect may miss early actions.
- The callback must be `async`. Use the existing event loop — do not spin up a new one.
- Bridge to blocking connector calls with `await asyncio.to_thread(...)` if the underlying client is synchronous.
- Custom actions are independent of `importer_io[].control` (which is for control-change messages on a mapped stream).

## requirements.txt and Local Config

Include the Kelvin SDK plus the external connector dependency set.

For an MQTT importer:

```text
kelvin-python-sdk[ai]
aiomqtt>=2.3.0
```

For an OPC-UA importer:

```text
kelvin-python-sdk[ai]
asyncua>=1.1.5
```

For local development, use `config.yaml` only to override `defaults.configuration` values. Do not commit secrets unless the repository explicitly permits local-only samples.

```yaml
mqtt:
  host: localhost
reconnect_interval: 5
```

An OPC-UA local override would look like:

```yaml
opcua:
  url: opc.tcp://localhost:4840
poll_interval: 5
```

## Checklist and Pitfalls

- Use `type: importer`, not `type: app`.
- Use `importer_io`, not `data_streams.inputs`, to describe ingest capability.
- Keep `ui_schemas.io_configuration` aligned with `importer_io[].name`.
- Read per-stream mapping from `asset_info.datastreams[stream_name].configuration`.
- Re-read `app.app_configuration` inside reconnect loops when runtime configuration updates are enabled.
- Catch `OSError` in addition to connector-library exceptions for network failures.
- Declare custom actions under the top-level `custom_actions:` block (not inside `importer_io`) and wire `app.on_custom_action` before `await app.connect()`; reply with `CustomActionResult` for every input.