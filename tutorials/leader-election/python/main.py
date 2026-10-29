"""Publish a heartbeat only while this Workload holds leadership."""

import asyncio

from kelvin.application import KelvinApp
from kelvin.ha import AsyncLeaderElection
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import Number

app = KelvinApp()
leader = AsyncLeaderElection()  # Reads the Workload's identity and Leader API address.


@leader.on_promoted
async def on_promoted() -> None:
    """Log the transition to active publishing."""
    logger.info("publisher_promoted")


@leader.on_demoted
async def on_demoted() -> None:
    """Log the transition to standby."""
    logger.info("publisher_demoted")


@app.task
async def maintain_election() -> None:
    """Run check-ins while the App runs; demote locally when the task stops."""
    await leader.start()
    try:
        await asyncio.Event().wait()  # Keep this task alive until the SDK cancels it.
    finally:
        await leader.stop()


@app.timer(interval=5)
async def publish_heartbeat() -> None:
    """Publish one heartbeat per assigned Asset while the lease is valid."""
    for asset in app.assets:
        if not leader.is_leader:  # Recheck before each publish; don't cache this value.
            return
        await app.publish(
            Number(
                resource=KRNAssetDataStream(asset, "publisher_heartbeat"), payload=1.0
            )
        )


if __name__ == "__main__":
    app.run()
