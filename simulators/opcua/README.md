# OPC-UA Machine Simulator
This is a Docker application that simulates fleets of industrial machines as an OPC-UA server: beam pumps, PCPs, and compressors by default, or any machine you describe in the configuration. Measurement tags move with configurable waveforms; setpoint and command tags are writable by clients, so both the ingest and the control directions of a Kelvin demo work against it.

Everything is driven by the application **configuration** (with a full UI form at deployment), not environment variables: assets, tags, waveforms, port, and authentication.

## Requirements
1. Python 3.9 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Docker (optional) for uploading the application to a Kelvin Instance.

## Connecting
Other workloads (and Kelvin OPC-UA connections) reach the server at:

```
opc.tcp://<workload-name>:50000
```

The server is not reachable from outside the cluster by default. To expose it, add a host-type port on the deployment (commented example in `app.yaml`); enable authentication before doing so.

## What a default deployment simulates
Five assets, each in its own folder with string NodeIds like `beampump01.spm` (namespace `http://kelvininc.com/opcua-simulator`):

- `BeamPump01`, `BeamPump02`: SPM, pump fillage, rod load, tubing/casing pressure, motor current; writable `spm_setpoint` and `run_command`.
- `PCP01`, `PCP02`: rotor speed, torque, motor temperature, tubing pressure, flow rate; writable `speed_setpoint` and `run_command`.
- `Compressor01`: suction/discharge pressure, discharge temperature, flow, vibration, surge margin; writable `speed_setpoint`, `recycle_valve_cmd`, `run_command`.

Assets with the same tag set never move in lockstep: each asset/tag pair derives its own seed and phase offset from `simulation.seed`, so runs are reproducible but units differ.

## Configuration reference

### `opcua`
| Option | Default | Notes |
|---|---|---|
| `port` | `50000` | Keep in sync with the service port in `app.yaml`. |
| `advertised_host` | empty | Hostname the server advertises in `GetEndpoints` (clients reconnect to it). Empty → the workload name (`KELVIN_WORKLOAD_NAME`), which is the service DNS name; only override for non-standard setups. |
| `auth.username` / `auth.password` | empty | Both set → anonymous access is disabled and clients must authenticate. Wire to secrets: `<% secrets.opc-sim-user %>` / `<% secrets.opc-sim-password %>`. |

### `simulation`
| Option | Default | Notes |
|---|---|---|
| `tick` | `1.0` | Seconds between value updates. |
| `seed` | `42` | Base seed; same seed → same series. |

### `assets[]`
| Option | Default | Notes |
|---|---|---|
| `name` | required | Asset type / NodeId prefix; instances are `<Name>01..NN`. |
| `count` | `1` | Number of identical assets to create. |
| `tags` | required | Map of tag name → tag spec (at least one). |

### Tag spec
| Option | Default | Notes |
|---|---|---|
| `waveform` | `constant` | `sine` \| `ramp` \| `square` \| `random_walk` \| `random` \| `constant` |
| `type` | `float` | `float` \| `int` \| `bool` (bools pair with `square`/`random`/`constant`) |
| `min` / `max` | `0` / `100` | Bounds; noise and walks are clamped/reflected inside them. |
| `period` | `60` | Seconds per cycle (`sine`, `ramp`, `square`). |
| `noise` | `0` | Gaussian noise added to the waveform. |
| `initial` | midpoint | Starting value; the whole story for `constant` and writable tags. |
| `writable` | `false` | `true` → the simulator never touches it; clients write it (setpoints/commands). Mutually exclusive with a waveform. |
| `unit` | none | Appended to the description and published as an `EngineeringUnits` property. |
| `description` | tag name | Node description. |

### Adding assets
Assets are plain configuration; add a new entry on the deployment (UI form or YAML) and redeploy:

```yaml
assets:
  - name: ESP
    count: 4
    tags:
      intake_pressure:    { waveform: random_walk, min: 900, max: 1600, unit: "psi" }
      motor_temp:         { waveform: ramp, min: 90, max: 140, period: 7200, unit: "degC" }
      frequency_setpoint: { writable: true, initial: 55.0, unit: "Hz" }
```

The address space is built at startup, so configuration changes apply on redeploy (`enable_runtime_update.configuration: false`).

### Environment variable overrides
Any configuration value can also be overridden with nested env vars (`__` as delimiter), which take precedence over the YAML: `OPCUA__AUTH__PASSWORD=...`, `SIMULATION__TICK=0.5`.

## Kelvin Cloud Deployment
To enable authentication, store the credentials as Secrets and reference them in the configuration on the deployment:

```
kelvin secret create opc-sim-user --value "<username>"
kelvin secret create opc-sim-password --value "<password>"
```

## Security notes
The server runs the unsecured OPC-UA transport (security policy `None`), matching this catalog's open-by-default posture; with authentication enabled, credentials still apply but travel unencrypted. Keep it cluster-internal, or front it with the secure policies if you extend the app.

## Local development

```sh
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python main.py           # picks up defaults from ./app.yaml
```

Configuration resolution order: env vars → `config.yaml` (platform-delivered at `/opt/kelvin/app/config.yaml`, or a local file) → the bundled `app.yaml` `defaults.configuration`. To emulate a deployment locally, write a `config.yaml` next to `main.py`.

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

Specific to this app:
- `settings.py`: the `opcua` configuration section (loaded via the SDK's `KelvinAppConfig`).
- `server.py`: the OPC-UA protocol adapter; address space, writable nodes, auth, endpoint advertisement, update loop.
- `main.py`: entry point.
