"""Serve metrics locally after replaying two motor readings through the App."""

import asyncio
from pathlib import Path

from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number
from kelvin.testing import KelvinAppTest, ManifestBuilder
from main import app, registry
from prometheus_client import start_http_server


async def replay() -> None:
    """Process two readings and keep the loopback metrics server available until Ctrl+C."""
    manifest = (
        ManifestBuilder.from_app_yaml(Path(__file__).with_name("app.yaml"))
        .add_asset("motor-01")
        .build()
    )
    server, thread = start_http_server(9100, addr="127.0.0.1", registry=registry)
    try:
        async with KelvinAppTest(app, manifest=manifest) as harness:
            for temperature in (45.0, 48.0):
                await harness.publish(
                    Number(
                        resource=KRNAssetDataStream("motor-01", "motor_temperature"),
                        payload=temperature,
                    )
                )
            await harness.run_until_idle()
            print(
                "Two readings processed; metrics available on port 9100. Press Ctrl+C to stop.",
                flush=True,
            )
            await asyncio.Event().wait()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    try:
        asyncio.run(replay())
    except KeyboardInterrupt:
        pass
