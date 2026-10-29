# Read runtime context

Read three scopes without changing their values: Workload-wide App Configuration, per-Asset App Parameters, and optional Asset Properties.

## Setup and tests

From this directory, with the Kelvin SDK CLI installed:

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

Tests cover initial and changed configuration, unchanged context, independent Asset limits, removal, and optional manufacturer metadata. Each test supplies every declared context field, including per-Asset parameters.

## Run locally

First terminal:

```bash
kelvin app test simulator --config app.yaml --asset-count 1
```

Second terminal, from the same directory:

```bash
uv run --no-project python main.py
```

Expect separate `motor_configuration`, `motor_parameter`, and `motor_manufacturer` events. Both temperature limits default to 80, but neither overrides the other; the manufacturer defaults to `unknown`. Stop both processes with Ctrl+C.

To override shared configuration locally, create `config.yaml` in the Publisher's directory:

```yaml
processing:
  temperature_limit: 75
```

Restart the Publisher and App. `motor_configuration` should show 75; the per-Asset parameter remains 80. The file replaces the entire shared configuration mapping. Remove the local file before uploading; it is not a deployment secret mechanism.

## Limits

This is a local teaching example without container packaging. Platform configuration delivery needs separate deployment verification. Use the parameter-reset tutorial for an explicit update request; this example only reads context.
