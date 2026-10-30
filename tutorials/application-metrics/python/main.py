"""Expose processing metrics for motor temperature readings."""

from kelvin.application import KelvinApp
from kelvin.message import AssetDataMessage
from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server

app = KelvinApp()
registry = CollectorRegistry()  # Keep these metrics separate from default collectors.
readings = Counter(
    "motor_temperature_readings_total",
    "Temperature readings processed",
    registry=registry,
)
latest_temperature = Gauge(
    "motor_temperature_latest_celsius",
    "Latest processed temperature in Celsius",
    registry=registry,
)


@app.stream(inputs=["motor_temperature"])
async def record_temperature(message: AssetDataMessage) -> None:
    """Record a reading from the single motor assigned to this App."""
    latest_temperature.set(message.payload)  # Can rise or fall with each reading.
    readings.inc()  # Increases once per processed reading.


if __name__ == "__main__":
    # Bind to all container interfaces so the Cluster can reach this port.
    server, thread = start_http_server(9100, addr="0.0.0.0", registry=registry)
    try:
        app.run()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
