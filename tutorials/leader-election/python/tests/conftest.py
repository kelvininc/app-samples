"""Supply test-only election settings before main.py constructs its client."""

import os

# No request reaches this address; every test injects an HTTP transport.
os.environ.update(
    {
        "KELVIN_CLUSTER_NAME": "test-cluster",
        "KELVIN_WORKLOAD_NAME": "heartbeat-01",
        "KELVIN_APP_NAME": "elected-heartbeat",
        "KELVIN_LEADER_API_ADDRESS": "127.0.0.1",
        "KELVIN_LEADER_API_PORT": "9000",
    }
)
