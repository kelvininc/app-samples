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

## Local demonstration

The tests supply runtime topic mappings through KelvinAppTest and mock the MQTT boundary. The generic App simulator does not support this Importer manifest. Run this bounded exercise after setup:

```bash
uv run --no-project pytest -q
```

For a real broker integration test, provide MQTT_TEST_HOST and MQTT_TEST_PORT, then run `uv run --no-project pytest -m integration -q`. It creates a unique topic and clears its retained value after the test.

## Packaging and limits

A Dockerfile is included. Build from this directory and verify the declared runtime configuration before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
