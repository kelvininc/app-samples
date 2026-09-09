# MQTT Machine Simulator
This is a Docker application that simulates fleets of industrial machines (beam pumps, PCPs, and compressors by default, or any asset you describe) and publishes their telemetry to an MQTT broker. Measurement tags move with configurable waveforms; setpoint and command tags are published at their initial value.

It pairs with the `mqtt-mosquitto` broker and the MQTT importer: simulator → broker → importer → Kelvin, all samples in this repo.

Everything is driven by the application **configuration** (with a full UI form at deployment): broker connection, publishing, and the asset/tag model.

## Requirements
1. Python 3.9 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Docker (optional) for uploading the application to a Kelvin Instance.

## What a default deployment publishes
Five assets (`BeamPump01/02`, `PCP01/02`, `Compressor01`), each tag once per tick to a topic like:

```
sim/beampump01/spm            {"value": 7.83, "timestamp": "..."}
sim/beampump01/pump_fillage   {"value": 87.4, "timestamp": "..."}
```

Assets with the same tag set never move in lockstep: each asset/tag pair derives its own seed and phase offset from `simulation.seed`.

## Consuming it with the MQTT importer
The default layout maps the whole fleet with a single importer io row: topic `sim/{asset}/{stream}`, `payload_field: value`. Name the Kelvin assets `beampump01`, `pcp01`, etc. to match.

## Configuration reference

### `mqtt`
| Option | Default | Notes |
|---|---|---|
| `host` | `mqtt-broker` | Broker to publish to; the workload name of the `mqtt-mosquitto` app on the same cluster. |
| `port` | `21883` | Broker port. |
| `client_id` | `mqtt-simulator` | MQTT client identifier. |
| `use_tls` | `false` | Connect over TLS (default SSL context); pair with the broker's secure port. |
| `auth.username` / `auth.password` | empty | Both set → authenticated; neither → anonymous. Wire to secrets: `<% secrets.mqtt-sim-user %>` / `<% secrets.mqtt-sim-password %>`. |

### `mqtt.publish`
| Option | Default | Notes |
|---|---|---|
| `topic` | `sim/{asset}/{tag}` | Topic template; `{asset}`/`{tag}` expand per value. `{tag}` is invalid with `payload: json_bundle`. |
| `qos` | `0` | MQTT QoS (0, 1, or 2). |
| `payload` | `json` | `raw` → `7.83`; `json` → `{"value": ..., "timestamp": ...}`; `json_bundle` → one message per asset with all its tags. |
| `timestamp` | `iso` | `iso`, `epoch_ms`, or `none` (ignored when `payload: raw`). |

### `simulation` and `assets`
Identical to the other machine simulators. `simulation` sets `tick` (seconds between updates) and `seed`. `assets` is a list of groups, each with a `name`, `count`, and `tags`; every tag has a `waveform` (`sine`/`ramp`/`square`/`random_walk`/`random`/`constant`), bounds, and optional `unit`. Writable tags (setpoints/commands) publish at their `initial` value. See the app's `app.yaml` for the full default set and the OPC-UA simulator's README for the complete tag-spec reference.

### Environment variable overrides
Any value can be overridden with nested env vars (`__` delimiter): `MQTT__AUTH__PASSWORD=...`, `SIMULATION__TICK=0.5`.

## Kelvin Cloud Deployment
To authenticate to the broker, store the credentials as Secrets and reference them in the configuration:

```
kelvin secret create mqtt-sim-user --value "<username>"
kelvin secret create mqtt-sim-password --value "<password>"
```

## Local development

```sh
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# point at a broker (defaults to host "mqtt-broker"); e.g. a local mosquitto:
MQTT__HOST=localhost MQTT__PORT=1883 ./.venv/bin/python main.py
```

Run the tests:

```sh
./.venv/bin/pip install pytest pytest-asyncio
./.venv/bin/python -m pytest tests/ -v
```

## Code layout
Shared across the simulator apps (copied verbatim; keep in sync, like the exporters' `drain.py`):
- `models.py`: the `simulation`/`assets` configuration models and validators.
- `waveforms.py`: deterministic waveform generators (`TagSimulator`).
- `fleet.py`: asset expansion, per-asset seeding, and sampling (`Fleet`).
- `messages.py`: topic/key rendering and payload encoding (shared with the Kafka simulator).

Specific to this app:
- `settings.py`: the `mqtt` connection/publish section (loaded via the SDK's `KelvinAppConfig`).
- `publisher.py`: the MQTT protocol adapter; connect and publish each tick.
- `main.py`: entry point.
