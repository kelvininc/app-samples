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

## Local demonstration

The tests supply runtime topic mappings and verify encoded records and producer lifecycle through KelvinAppTest. The generic App simulator does not support this Exporter manifest. Run this bounded exercise after setup:

```bash
uv run --no-project pytest -q
```

For a real broker integration test, provide KAFKA_TEST_BOOTSTRAP_SERVERS, then run `uv run --no-project pytest -m integration -q`. Use an isolated broker: the test creates a unique topic.

## Packaging and limits

A Dockerfile is included. Build from this directory and verify the declared runtime configuration before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
