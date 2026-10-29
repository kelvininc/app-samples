# Leader election

Publish heartbeats only while this Workload holds leadership. Local tests mock the Leader API boundary; platform failover and network-loss behavior require separate verification.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv sync
uv run pytest -q
```

## Local demonstration

The tests drive promotion, demotion, cancellation, and publishing through a mocked Leader API boundary. Without platform election configuration and a reachable Leader API, local election is disabled and publishes no heartbeat. Run this bounded exercise after setup:

```bash
uv run pytest -q
```

For deployment, use the Leader API configuration supplied by Kelvin. A local Publisher supplies App context, not the election service or Workload identity.

## Packaging and limits

This is a local SDK teaching example. Add and verify container packaging before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
