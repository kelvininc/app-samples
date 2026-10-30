# Work Order Exporter

Handle create_work_order through an HTTPS adapter, validate equipment_id and summary, and return the adapter Work Order ID. Set adapter_url and WORK_ORDER_TOKEN. Tests use HTTP mocks, not SAP or ServiceMax.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

## Local demonstration

The tests inject create_work_order and mock the HTTPS adapter. They check payload validation, idempotency headers, successful IDs, and unconfirmed outcomes. The generic App simulator does not support this Exporter manifest. Run this bounded exercise after setup:

```bash
uv run --no-project pytest -q
```

To run main.py as a real Application, use the message-specific deployment setup in Developer Tools. The local harness demonstration does not establish platform delivery or external execution.

## Packaging and limits

This is a local SDK teaching example. Add and verify container packaging before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
