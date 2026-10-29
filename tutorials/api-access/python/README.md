# Kelvin API access

Read one page of Assets through the App API client. The local authenticated runner prompts for a URL and token; unavailable client configuration is logged without making a request.

## Setup and tests

Run from this directory. Install the Kelvin SDK CLI through the Developer Tools installation guide.

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

## Local run

For SDK-managed local context, start the Kelvin SDK CLI Publisher in a separate terminal from this directory:

```bash
kelvin app test simulator --config app.yaml --asset-count 1
```

Then start the App in the prepared environment:

```bash
uv run --no-project python run_local_api.py
```

The runner prompts for a Kelvin Cloud URL and hides the access token as you type. Use a token authorized to read Assets. Running main.py without an injected or configured API client instead logs kelvin_api_not_configured. Stop both processes with Ctrl+C. This supplies local context, not the external services or full workflow.

## Packaging and limits

This is a local SDK teaching example. Add and verify container packaging before deployment.

See [Developer Tools](https://docs.kelvin.ai/latest/developer-tools/) for setup and message-specific delivery contracts.
