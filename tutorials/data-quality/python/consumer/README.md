# Data Quality consumer

Publish or consume quality scores using the declared metric resource scope. Read the manifest for Asset-level and Asset Data Stream metric declarations. Local tests do not establish Broker routing.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

## Local demonstration

The harness injects Asset and Asset Data Stream quality resources and checks their scope, metric, and value logs. A generic local simulator does not generate quality resources. Run this bounded exercise after setup:

```bash
uv run --no-project pytest -q
```

To run main.py as a real Application, use the message-specific deployment setup in Developer Tools. The local harness demonstration does not establish platform delivery or external execution.

## Packaging and limits

This is a local SDK teaching example. Add and verify container packaging before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
