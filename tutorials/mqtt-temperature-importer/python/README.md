# MQTT temperature Importer

Import numeric MQTT payloads into runtime-mapped Asset Data Streams. Local tests mock MQTT; a real broker and mapped topic configuration are required for ingestion.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

## Broker prerequisites

Start a local MQTT broker and configure mqtt_host/mqtt_port in the manifest defaults or a local config.yaml. Every mapped input needs its MQTT topic in the I/O configuration. simulate_sensor.py supplies numeric test readings once the broker exists. The simulator alone does not create a broker or equipment mapping.

## Local run

For SDK-managed local context, start the Kelvin SDK CLI Publisher in a separate terminal from this directory:

```bash
kelvin app test simulator --config app.yaml --asset-count 1
```

Then start the App in the prepared environment:

```bash
uv run --no-project python main.py
```

Stop both processes with Ctrl+C. This supplies local context, not the external services or full workflow.

## Packaging and limits

A Dockerfile is included. Build from this directory and verify the declared runtime configuration before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
