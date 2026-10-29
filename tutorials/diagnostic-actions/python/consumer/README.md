# Diagnostic Custom Actions consumer

Request run_diagnostics or echo its validated include_history flag. Match types and action IDs; the consumer performs no equipment diagnostics. Local tests do not establish manager delivery.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

## Local demonstration

The harness injects run_diagnostics and checks the correlated result, including rejection of a non-Boolean include_history. A generic local simulator does not generate Custom Actions. Run this bounded exercise after setup:

```bash
uv run --no-project pytest -q
```

To run main.py as a real Application, use the message-specific deployment setup in Developer Tools. The local harness demonstration does not establish platform delivery or external execution.

## Packaging and limits

This is a local SDK teaching example. Add and verify container packaging before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
