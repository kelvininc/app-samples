# Kafka Machine Simulator
This is a Docker application that simulates fleets of industrial machines (beam pumps, PCPs, and compressors by default, or any asset you describe) and produces their telemetry to Kafka topics. Measurement tags move with configurable waveforms; setpoint and command tags are produced at their initial value.

It pairs with the `kafka` broker and the Kafka importer: simulator → broker → importer → Kelvin, all samples in this repo.

Everything is driven by the application **configuration** (with a full UI form at deployment): broker connection, publishing, and the asset/tag model.

## Requirements
1. Python 3.9 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Docker (optional) for uploading the application to a Kelvin Instance.

## What a default deployment produces
Five assets (`BeamPump01/02`, `PCP01/02`, `Compressor01`). Each tag is one record per tick on a per-asset topic, keyed by tag name:

```
topic sim.BeamPump01   key spm             {"value": 7.83, "timestamp": "..."}
topic sim.BeamPump01   key pump_fillage    {"value": 87.4, "timestamp": "..."}
```

One topic per asset (not per tag) avoids topic proliferation; the tag rides in the record key. Assets with the same tag set never move in lockstep: each asset/tag pair derives its own seed and phase offset from `simulation.seed`.

## Consuming it with the Kafka importer
The default layout maps the whole fleet with a single importer io row: topic `sim.{asset}`, key `{stream}`, `payload_field: value`. Name the Kelvin assets `beampump01`, `pcp01`, etc. to match.

## Configuration reference

### `kafka`
| Option | Default | Notes |
|---|---|---|
| `bootstrap_servers` | `my-kafka:9092` | The workload name and service port of the `kafka` broker app on the same cluster. |
| `security` | PLAINTEXT | `protocol` (`PLAINTEXT`/`SSL`/`SASL_PLAINTEXT`/`SASL_SSL`) with matching `sasl` and `tls` blocks. Identical to the Kafka connectors, so a broker's config is copy-pasteable. |

`security.sasl`: `mechanism` (`PLAIN`/`SCRAM-SHA-256`/`SCRAM-SHA-512`), `username`, `password` (all three together). `security.tls`: `ca_cert`, `client_cert`, `client_key` as PEM content (wire to secrets); `ca_cert` replaces the system trust store, `client_cert`+`client_key` enable mTLS.

### `kafka.publish`
| Option | Default | Notes |
|---|---|---|
| `topic` | `sim.{asset}` | Topic template; `{asset}`/`{tag}` expand per record. `{tag}` is invalid with `payload: json_bundle`. |
| `key` | `{tag}` | Record-key template; the importer filters/demuxes on it. Same placeholder rule. |
| `payload` | `json` | `raw` → `7.83`; `json` → `{"value": ..., "timestamp": ...}`; `json_bundle` → one record per asset with all its tags. |
| `timestamp` | `iso` | `iso`, `epoch_ms`, or `none` (ignored when `payload: raw`). |

### `simulation` and `assets`
Identical to the other machine simulators. `simulation` sets `tick` and `seed`. `assets` is a list of groups, each with a `name`, `count`, and `tags`; every tag has a `waveform` (`sine`/`ramp`/`square`/`random_walk`/`random`/`constant`), bounds, and optional `unit`. Writable tags (setpoints/commands) publish at their `initial` value. See `app.yaml` for the full default set and the OPC-UA simulator's README for the complete tag-spec reference.

### Environment variable overrides
Any value can be overridden with nested env vars (`__` delimiter): `KAFKA__SECURITY__SASL__PASSWORD=...`, `SIMULATION__TICK=0.5`.

## Kelvin Cloud Deployment
To authenticate, store the credentials as Secrets and reference them in the `security` block:

```
kelvin secret create kafka-sim-user --value "<username>"
kelvin secret create kafka-sim-password --value "<password>"
```

## Local development

```sh
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# point at a broker (defaults to my-kafka:9092); e.g. a local kafka:
KAFKA__BOOTSTRAP_SERVERS=localhost:9092 ./.venv/bin/python main.py
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
- `messages.py`: topic/key rendering and payload encoding (shared with the MQTT simulator).

Specific to this app:
- `settings.py`: the `kafka` connection/publish section (loaded via the SDK's `KelvinAppConfig`).
- `publisher.py`: the Kafka protocol adapter; connect and produce each tick.
- `main.py`: entry point.
