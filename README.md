![Kelvin Logo](logo.png)

# Welcome to Kelvin SDK - App Samples
This repository contains sample applications that demonstrate how to use the **Kelvin SDK**.

Start with the official [Kelvin Documentation](https://docs.kelvin.ai).

# Sample Applications

## 🚀 SmartApps

| Application                                                                      | Domain               | Level        | Description                                                                                         |
|--------------------------------------------------------------------------------- |----------------------|--------------|-----------------------------------------------------------------------------------------------------|
| [Casting Defect Detection](applications/casting-defect-detection/)               | Computer Vision      | Advanced     | Uses a TensorFlow-based model to identify and analyze manufacturing defects in casting processes.   |
| [Event Detection](applications/event-detection/)                                 | Event Detection      | Beginner     | Monitors streaming data for threshold-crossing events and emits Control Changes or Recommendations. |
| [Multi-Objective Optimization ML](applications/multi-objective-optimization-ml/) | Machine Learning     | Advanced     | Solves multi-objective optimization problems using ML techniques.                                   |

## 📥 Importers

| Application                                  | Level        | Description                                                                                             |
|----------------------------------------------|--------------|---------------------------------------------------------------------------------------------------------|
| [Kafka Importer](importers/kafka/)           | Intermediate | Bidirectional Kafka connector that consumes records into Kelvin and writes control changes back.       |
| [MQTT Importer](importers/mqtt/)             | Intermediate | Bidirectional MQTT connector that ingests broker messages into Kelvin and writes control changes back. |
| [Image Feed Importer](importers/image-feed/) | Intermediate | Replays a folder of images to Kelvin as a camera feed.                                                  |

## 📤 Exporters

| Application                                                            | Level        | Description                                                                          |
|------------------------------------------------------------------------|--------------|--------------------------------------------------------------------------------------|
| [AWS S3 Exporter](exporters/aws-s3/)                                   | Intermediate | Uploads streaming data to an AWS S3 bucket.                                          |
| [Azure Data Lake Exporter](exporters/azure-data-lake/)                 | Intermediate | Uploads streaming data to Azure Data Lake Storage Gen2.                              |
| [Databricks Delta Table Exporter](exporters/databricks-delta-table/)   | Intermediate | Uploads streaming data to a Databricks Delta Table.                                  |
| [Databricks Volume Exporter](exporters/databricks-volume/)             | Intermediate | Uploads streaming data to a Databricks Volume.                                       |
| [Databricks Zerobus Exporter](exporters/databricks-zerobus/)           | Intermediate | Streams data into a Databricks Unity Catalog Delta table via the Zerobus Ingest API. |
| [Kafka Exporter](exporters/kafka/)                                     | Intermediate | Publishes asset data to Kafka topics, one JSON message per record.                   |
| [Snowflake Exporter](exporters/snowflake/)                             | Intermediate | Uploads streaming data to a Snowflake table.                                         |
| [SFTP Exporter](exporters/sftp/)                                       | Intermediate | Uploads batched files to an SFTP server.                                             |
| [Email Exporter](exporters/email-action/)                              | Intermediate | Sends email notifications via a custom action (SMTP).                               |
| [Slack Exporter](exporters/slack-action/)                              | Intermediate | Posts Slack messages via a custom action.                                            |
| [Microsoft Teams Exporter](exporters/microsoft-teams-action/)          | Intermediate | Posts Microsoft Teams messages via a custom action.                                  |

## 🤖 Docker

| Application                                     | Level        | Description                                                              |
|-------------------------------------------------|--------------|---------------------------------------------------------------------------|
| [MQTT Mosquitto Broker](docker/mqtt-mosquitto/) | Intermediate | Mosquitto MQTT broker that supports SSL/TLS encryption.                  |
| [Kafka Broker](docker/kafka/)                   | Intermediate | Single-node Apache Kafka broker (KRaft) with optional SASL auth and TLS. |
| [Redis Server](docker/redis/)                   | Intermediate | Single-node Redis server with optional authentication and TLS.           |

## 🏭 Simulators

| Application                                          | Level        | Description                                                                              |
|------------------------------------------------------|--------------|-------------------------------------------------------------------------------------------|
| [Kafka Machine Simulator](simulators/kafka/)         | Intermediate | Simulates fleets of industrial machines producing telemetry to Kafka topics.            |
| [MQTT Machine Simulator](simulators/mqtt/)           | Intermediate | Simulates fleets of industrial machines publishing telemetry to an MQTT broker.         |
| [OPC-UA Machine Simulator](simulators/opcua/)        | Intermediate | Simulates fleets of industrial machines as an OPC-UA server with writable setpoints.    |

# Running Samples Locally

Each sample is self-contained: its own `requirements.txt`, `Dockerfile`, and `app.yaml`.
We use [uv](https://docs.astral.sh/uv/) to install the `kelvin` CLI and to run each app in an
isolated environment built from its existing `requirements.txt`. This doesn't change how apps
build and run on Kelvin.

Two packages are involved, each with a different job:

| Package             | Role                                              | Where it lives                |
|---------------------|--------------------------------------------------|-------------------------------|
| `kelvin-sdk`        | The `kelvin` CLI to build, test, and deploy apps | Installed once, globally      |
| `kelvin-python-sdk` | The runtime library each app imports             | Each app's `requirements.txt` |

All samples target **Python 3.13**.

## Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install the Kelvin
CLI once, globally:

```bash
uv tool install kelvin-sdk     # provides the `kelvin` command
kelvin --version               # verify it's on your PATH
```

Upgrade it later with `uv tool upgrade kelvin-sdk`.

## Run a sample

From inside any app folder:

```bash
cd exporters/aws-s3

uv venv --python 3.13              # create an isolated environment
uv pip install -r requirements.txt # install kelvin-python-sdk + the app's deps
uv run python main.py              # run the app
```

`uv run` auto-detects the local `.venv`, so there's no need to activate it. After setup,
re-running an app is just `uv run python main.py`.

To feed an app simulated data, open a second terminal and use the CLI:

```bash
kelvin app test simulator
```

To skip keeping a `.venv`, run the app ephemerally; uv builds a throwaway
environment each time:

```bash
uv run --python 3.13 --with-requirements requirements.txt python main.py
```

# Contributing

Please read our [Style Guide](CONTRIBUTING.md) before contributing a new sample application.

1. Fork the project.
2. Create your feature branch (git checkout -b feature/YourFeature).
3. Follow the [Style Guide](CONTRIBUTING.md) for folder structure, naming conventions, and code style.
4. Commit your changes (git commit -m 'Add some feature').
5. Push to the branch (git push origin feature/YourFeature).
6. Open a pull request.