# Leader election

Publish heartbeats only while this Workload holds leadership. Local tests mock the Leader API boundary; platform failover and network-loss behavior require separate verification.

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
