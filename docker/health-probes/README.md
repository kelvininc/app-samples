# Health probes

Serve separate liveness and readiness responses using nginx. Build and run the Dockerfile locally; this does not establish Kelvin routing or probe reconciliation.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
docker build -t kelvin-health-probes .
```

## Local run

```bash
docker run --rm -p 8080:8080 kelvin-health-probes
```

## Packaging and limits

A Dockerfile is included. Build from this directory and verify the declared runtime configuration before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
