# Insights consumer

Publish or consume temperature_high Insights. The producer uses the reading timestamp; the consumer logs identity and acceptance state. Local tests do not establish platform polling/delivery.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

## Local demonstration

The harness injects a temperature_high Insight and checks its Asset, tag name, and acceptance log. A generic local simulator does not generate Insights. Run this bounded exercise after setup:

```bash
uv run --no-project pytest -q
```

To run main.py as a real Application, use the message-specific deployment setup in Developer Tools. The local harness demonstration does not establish platform delivery or external execution.

## Packaging and limits

This is a local SDK teaching example. Add and verify container packaging before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
