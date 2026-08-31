# Event Detection
This application demonstrates the use of the Kelvin SDK for detecting threshold-crossing events on an asset stream and recommending a control change.

It watches each asset's `motor_temperature` stream. When a reading exceeds the asset's `temperature_max_threshold`, it recommends dropping the motor speed to `speed_decrease_set_point`. The recommendation carries the control change; the per-asset `kelvin_closed_loop` parameter decides whether Kelvin auto-accepts it or leaves it for an operator to approve.

## How It Works
- `@app.stream(inputs=["motor_temperature"])` receives every temperature reading.
- The handler reads the asset's parameters (`temperature_max_threshold`, `speed_decrease_set_point`, `kelvin_closed_loop`); parameters are runtime-updatable, so operators can retune an asset without a redeploy.
- Above threshold, it publishes a `Recommendation` with an embedded `motor_speed_set_point` `ControlChange`. `auto_accepted` is set from `kelvin_closed_loop`, so a closed-loop asset applies the change automatically while an open-loop asset waits for approval.

## Prerequisites
1. Python 3.13 (the version the app is built and tested on; see the `Dockerfile`).
2. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
3. Install project dependencies: `pip3 install -r requirements.txt`.
4. Docker (optional) to upload the application to Kelvin Cloud.

## Run Locally
Asset parameters come from the platform at runtime; locally they fall back to the `defaults.parameters` in `app.yaml`.

1. **Run** the application: `python3 main.py`
2. Open a new terminal and feed it data. Either stream synthetic values:
    ```
    kelvin app test simulator
    ```
   or replay the bundled sample data:
    ```
    kelvin app test csv --csv csv/test.csv
    ```

## Test Locally
### Unit Tests
```bash
pip install 'kelvin-python-sdk[testing]'        # harness deps
pytest                                           # fast, no Docker
```
- **Unit** (`tests/test_main.py`): the temperature handler via `KelvinAppTest`, covering no recommendation at or below threshold, a `decrease_speed` recommendation with the embedded control change above it, and `kelvin_closed_loop` driving auto-accept.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it: map the `motor_temperature` input and `motor_speed_set_point` control change to each asset, and set the parameters (`temperature_max_threshold`, `speed_decrease_set_point`, `kelvin_closed_loop`) per asset. This app needs no secrets.
