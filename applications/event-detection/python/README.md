# Event Detection

Convert Motor 01 temperature readings to Fahrenheit and recommend a lower motor speed above its per-Asset threshold.

## Behavior

- Input: numeric `temperature_c`. Every reading produces numeric `temperature_f`, preserving its Asset and timestamp.
- At or below `temperature_max_threshold` (default 80 C), publish no Recommendation.
- Above the threshold, recommend `motor_speed_setpoint` using `speed_decrease_set_point` (default 1000). The Recommendation expires after five minutes; its nested change inherits expiry and requests no retries.
- `kelvin_closed_loop` defaults to false, so the Recommendation awaits approval. Acceptance is separate from equipment execution.

## Setup and tests

Install the Kelvin SDK CLI using the Developer Tools installation guide. From this directory:

```bash
uv venv
uv pip install -r requirements-dev.txt
uv run --no-project pytest -q
```

Tests cover exact boundaries, two Assets, source timestamps, expiry, closed-loop states, all evidence variants, CSV metadata, synthetic/random inputs, and a finite custom generator. `evidence_examples.py` demonstrates additional evidence models without making external requests.

## Run locally

In the first terminal, from this directory:

```bash
kelvin app test csv --config app.yaml --csv data/test-data.csv --assets data/assets.csv --ignore-timestamps --publish-interval 1 --replay
```

The CSV replays 45, 80, and 81 C for the Asset in `data/assets.csv`. In a second terminal, from this directory:

```bash
uv run --no-project python main.py
```

For interactive synthetic data instead of recorded replay, start the local Publisher with `kelvin app test simulator --config app.yaml --asset-count 1`. Stop the Publisher and App with Ctrl+C. Local processing does not establish cloud Recommendation delivery or equipment execution.

## Packaging

Build the included Dockerfile from this directory. Before uploading, verify its manifest and UI schemas using the current Kelvin SDK CLI. Deployment requires input/output mappings and a real receiver for the proposed Control Change; this sample contains no equipment driver.

## Source migration

This implementation combines the documentation's Event Detection and Recommendation examples. Its input is `temperature_c` (formerly `motor_temperature`); its setpoint output is `motor_speed_setpoint` (the older public sample used `motor_speed_set_point`). The default threshold is now 80 C, and expiry is five minutes. Update existing mappings when adopting it.
