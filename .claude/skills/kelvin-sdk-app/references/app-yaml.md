# app.yaml Configuration Reference

## When to Use

Use this file to create or edit `app.yaml`, declare inputs/outputs, configure defaults, and wire UI schemas for SmartApps (`type: app`).

## Table of Contents
- [app.yaml Configuration Reference](#appyaml-configuration-reference)
  - [When to Use](#when-to-use)
  - [Table of Contents](#table-of-contents)
  - [Full Template](#full-template)
  - [Data Types](#data-types)
  - [Naming Conventions](#naming-conventions)
  - [Configuration vs Parameters](#configuration-vs-parameters)
  - [API Permissions](#api-permissions)
  - [UI Schemas](#ui-schemas)

## Full Template

This template is for SmartApps (`type: app`).

```yaml
spec_version: 5.0.0
type: app

name: well-production-monitor
title: Well Production Monitoring
description: Monitors well production metrics and generates optimization recommendations
version: 1.0.0
category: smartapp

flags:
  enable_runtime_update:
    configuration: false   # Can update config at runtime (default: false)
    resources: false       # Can add/remove assets at runtime (default: false)
    parameters: true       # Can update parameters at runtime (default: true)
    resource_properties: true  # Can update asset properties at runtime (default: true)

data_streams:
  inputs:
    - name: casing_pressure
      data_type: number
    - name: tubing_pressure
      data_type: number
    - name: temperature
      data_type: number
    - name: oil_rate
      data_type: number
    - name: gas_rate
      data_type: number
    - name: choke_setpoint
      data_type: number
  outputs:
    - name: production_index
      data_type: number
    - name: optimization_status
      data_type: string

control_changes:
  inputs:
    - name: external_setpoint
      data_type: number
  outputs:
    - name: choke_setpoint
      data_type: number
    - name: speed_sp
      data_type: number

data_quality:
  inputs:
    - name: kelvin_timestamp_anomaly
      data_type: number
      data_streams: [casing_pressure, tubing_pressure, oil_rate, gas_rate]
    - name: kelvin_stale_detection
      data_type: number
      data_streams: [temperature]
    - name: kelvin_out_of_range_detection
      data_type: number
      data_streams: [casing_pressure]
    - name: kelvin_outlier_detection
      data_type: number
      data_streams: [oil_rate]
    - name: kelvin_data_availability
      data_type: number
      data_streams: [gas_rate]
    - name: kelvin_score
      data_type: number

parameters:  # Per-asset, can differ between assets
  - name: max_casing_pressure
    data_type: number
  - name: min_oil_rate
    data_type: number
  - name: max_water_cut
    data_type: number
  - name: kelvin_closed_loop
    data_type: boolean

custom_actions:
  inputs:
    - type: slack
  outputs:
    - type: email
    - type: work_order

data_tags:
  inputs:
    - cycle_detected
  outputs:
    - anomaly_window

api_permissions:  # Required when using app.api for platform reads/writes
  - kelvin.permission.storage.read
  - kelvin.permission.recommendation.create
  - kelvin.permission.control_change.read

ui_schemas:
  parameters: ui_schemas/parameters.json
  configuration: ui_schemas/configuration.json

defaults:
  parameters:
    max_casing_pressure: 1500.0
    min_oil_rate: 100.0
    max_water_cut: 0.80
    kelvin_closed_loop: false
  configuration:  # Global app-level, same for all assets (no declaration needed)
    analysis_window_minutes: 15
    optimization_interval_seconds: 300
  system:
    environment_vars:
      - name: WEATHER_API_KEY # Dummy env var to serve as an example
        value: <% secrets.weather-api-key %>
```

## Data Types

- `number`: numeric values (`int`, `float`)
- `string`: text values
- `boolean`: true/false values
- `object`: JSON objects

## Naming Conventions

- Use lowercase hyphenated names for apps (for example, `motor-monitor`).
- Use lowercase snake_case for stream, parameter, and variable names (for example, `motor_speed_set_point`).
- Use dots only when hierarchical naming helps clarity (for example, `sensor.temperature`).
- Validate names with `^[a-z0-9]([-_.a-z0-9]*[a-z0-9])?$`.

## Configuration vs Parameters

- Use `defaults.configuration` for global app settings (same for all assets).
- Use `parameters` for per-asset settings (values can differ per asset).
- Never declare a top-level `configuration:` schema section; put configuration values directly in `defaults.configuration`.

```python
from kelvin.application import KelvinApp
from kelvin.logs import logger

app = KelvinApp()

async def on_connect():
    # Global app-level configuration
    window_minutes = int(app.app_configuration.get("analysis_window_minutes", 15))
    optimization_interval = int(app.app_configuration.get("optimization_interval_seconds", 300))
    logger.info("App configuration", window_minutes=window_minutes, optimization_interval=optimization_interval)

    # Per-asset parameters
    for asset_name, asset in app.assets.items():
        max_casing_pressure = float(asset.parameters.get("max_casing_pressure", 1500.0))
        min_oil_rate = float(asset.parameters.get("min_oil_rate", 100.0))
        logger.info("Asset configured", asset=asset_name, max_pressure=max_casing_pressure)

app.on_connect = on_connect
app.run()
```

## API Permissions

When the app uses `app.api`, declare the required permissions in `app.yaml` under `api_permissions` at the root level. See [api-client.md — API Permissions](../kelvin-sdk/references/api-client.md#api-permissions) for details on how to find the correct permissions.

```yaml
api_permissions:
  - kelvin.permission.storage.read
  - kelvin.permission.recommendation.create
  - kelvin.permission.control_change.read
```

## UI Schemas

Configure the Kelvin UI with JSON schemas for parameters and configuration.

**parameters.json** (asset-level):
```json
{
  "type": "object",
  "properties": {
    "max_casing_pressure": {
      "type": "number",
      "title": "Max Casing Pressure (psi)",
      "description": "Alert when casing pressure exceeds this value",
      "minimum": 0,
      "maximum": 3000,
      "default": 1500.0
    },
    "kelvin_closed_loop": {
      "type": "boolean",
      "title": "Closed Loop Recommendations",
      "description": "Auto-accept recommendations that include actions",
      "default": false
    }
  },
  "required": ["max_casing_pressure", "kelvin_closed_loop"]
}
```

**configuration.json** (app-level):
```json
{
  "type": "object",
  "properties": {
    "analysis_window_minutes": {
      "type": "number",
      "title": "Analysis Window (minutes)",
      "description": "Time window for production data analysis",
      "minimum": 1,
      "maximum": 60,
      "default": 15
    }
  }
}
```
