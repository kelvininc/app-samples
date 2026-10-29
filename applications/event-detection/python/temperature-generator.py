import asyncio
from collections.abc import AsyncGenerator

from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number
from kelvin.publisher import DataGenerator
from typing_extensions import override


class MotorTemperatureGenerator(DataGenerator):
    """Replay a threshold-crossing sequence for one local test Asset."""

    @override
    async def run(self) -> AsyncGenerator[Number, None]:
        """Yield below, at, and above-threshold readings one second apart."""
        for value in [45.0, 80.0, 81.0]:
            yield Number(
                resource=KRNAssetDataStream("test-asset-1", "temperature_c"),
                payload=value,
            )
            await asyncio.sleep(1)
