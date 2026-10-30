# Application metrics

Expose a reading counter and latest-temperature gauge for a single assigned motor. The HTTP endpoint runs on port 9100; replay.py supplies a finite local demonstration.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv sync
uv run pytest -q
```

## Local run

For SDK-managed local context, start the Kelvin SDK CLI Publisher in a separate terminal from this directory:

```bash
kelvin app test simulator --config app.yaml --asset-count 1
```

Then start the App in the prepared environment:

```bash
uv run python main.py
```

Stop both processes with Ctrl+C. This supplies local context, not the external services or full workflow.

## Packaging and limits

This is a local SDK teaching example. Add and verify container packaging before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
