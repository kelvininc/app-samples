---
name: kelvin-sdk
description: Shared Kelvin SDK references for both SmartApps (`type: app`) and importers (`type: importer`). Covers the Kelvin API client, KRN construction/parsing, and cross-cutting best practices. This skill is loaded automatically by kelvin-sdk-app and kelvin-sdk-importer when shared references are needed — do not invoke it directly.
---

# Kelvin SDK — Shared References

This skill contains reference files shared by both SmartApp and importer workflows. It is not intended to be used directly. Use `kelvin-sdk-app` for SmartApps or `kelvin-sdk-importer` for importer applications.

## Shared References

- [references/api-client.md](references/api-client.md) — Kelvin API reads/writes (`app.api`) and timeseries queries.
- [references/krn.md](references/krn.md) — KRN construction, parsing, and supported types.
- [references/best-practices.md](references/best-practices.md) — Cross-cutting implementation checklist and common pitfalls.

## API Client Usage

Applies to any app type (`app` or `importer`) that uses `app.api`:

- Add the required `api_permissions` to `app.yaml` at the root level. Inspect the called method's docstring under `kelvin.api.client.api` for its **Permission Required**.
- Only declare permissions that are actually exercised by the app code.
- **Before constructing any API request payload or interpreting any response**, inspect the Pydantic models under `kelvin.api.client.model` (primarily `requests`, `responses`, `type`, and `enum`) to verify exact field names, types, required/optional status, and allowed enum values. Never guess field names or invent payload structures.
