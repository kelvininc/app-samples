# Kafka temperature Exporter

Send mapped numeric readings as JSON records to Kafka and await broker acknowledgement. This tutorial has no durable buffer; see exporters/kafka for the broader integration.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

## Broker prerequisites

Start a Kafka broker and configure bootstrap_servers in the manifest defaults or a local config.yaml. Configure a topic for each mapped output. consume_records.py can inspect broker records. The local Publisher alone does not start Kafka or create topic mappings.

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
