"""Verify decorated processing and the actual Prometheus HTTP response."""

from pathlib import Path
from urllib.request import urlopen

import pytest
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app, registry
from prometheus_client import start_http_server


class TestRecordTemperature:
    """Drive the registered stream handler through KelvinAppTest."""

    @pytest.mark.asyncio
    async def test_counter_and_latest_value(self) -> None:
        """Count two readings and retain the second, lower temperature."""
        before = registry.get_sample_value("motor_temperature_readings_total")
        manifest = (
            ManifestBuilder.from_app_yaml(Path("app.yaml"))
            .add_asset("motor-01")
            .build()
        )
        async with KelvinAppTest(app, manifest=manifest) as harness:
            for temperature in (48.0, 45.0):
                await harness.publish(
                    Number(
                        resource=KRNAssetDataStream("motor-01", "motor_temperature"),
                        payload=temperature,
                    )
                )
            await harness.run_until_idle()
            assert (
                registry.get_sample_value("motor_temperature_readings_total")
                == before + 2
            )
            assert registry.get_sample_value("motor_temperature_latest_celsius") == 45.0


class TestMetricsHTTP:
    """Exercise the library server on an ephemeral loopback port."""

    def test_exposition(self) -> None:
        """Serve the registered counter and gauge with their metric types."""
        server, thread = start_http_server(0, addr="127.0.0.1", registry=registry)
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/metrics", timeout=5
            ) as response:
                assert response.status == 200
                body = response.read().decode()
            assert "# TYPE motor_temperature_readings_total counter" in body
            assert "# TYPE motor_temperature_latest_celsius gauge" in body
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
