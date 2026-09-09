"""Asset fleet expansion and sampling, shared by the machine simulators.

This file is copied verbatim across the simulator apps (opcua, mqtt, kafka, ...);
keep changes in sync. Protocol adapters consume a Fleet: servers expose its
points and drive updates from `simulated`; publishers emit `sample(t)` readings.
"""

import zlib
from dataclasses import dataclass
from typing import Iterator, Optional

from models import AssetGroup, TagSpec
from waveforms import TagSimulator

Value = float | int | bool


@dataclass(frozen=True)
class Point:
    """One tag on one asset instance.

    Attributes:
        asset: Asset instance name, e.g. "BeamPump01".
        tag: Tag name, e.g. "spm".
        spec: The tag specification.
        simulator: Value generator; None for static (writable/constant) points.
    """

    asset: str
    tag: str
    spec: TagSpec
    simulator: Optional[TagSimulator]

    @property
    def point_id(self) -> str:
        """Stable lowercase identifier, e.g. "beampump01.spm" (NodeIds, keys)."""
        return f"{self.asset.lower()}.{self.tag}"


def initial_value(spec: TagSpec) -> Value:
    """Starting value for a point: the configured initial, or the range midpoint."""
    if spec.type == "bool":
        return bool(spec.initial) if spec.initial is not None else False
    raw = float(spec.initial) if isinstance(spec.initial, (int, float)) else (spec.min + spec.max) / 2
    return round(raw) if spec.type == "int" else raw


def _tag_seed(global_seed: int, asset_name: str, tag_name: str) -> int:
    # crc32 (not hash()) so seeds are stable across processes and restarts.
    return global_seed + zlib.crc32(f"{asset_name}/{tag_name}".encode())


class Fleet:
    """Expands asset groups into concrete, independently-seeded points.

    Each group produces `count` instances named `<Name>01..NN`; every
    asset/tag pair gets its own seed (and phase offset) derived from the
    global seed, so identical assets never move in lockstep and runs are
    reproducible.

    Parameters:
        assets: The configured asset groups.
        seed: The global simulation seed.
    """

    def __init__(self, assets: list[AssetGroup], seed: int) -> None:
        self.simulated: list[Point] = []
        self.static: list[Point] = []
        for group in assets:
            for instance in range(1, group.count + 1):
                name = f"{group.name}{instance:02d}"
                for tag_name, spec in group.tags.items():
                    if spec.writable:
                        self.static.append(Point(name, tag_name, spec, None))
                    else:
                        simulator = TagSimulator(spec, _tag_seed(seed, name, tag_name))
                        self.simulated.append(Point(name, tag_name, spec, simulator))

    @property
    def asset_count(self) -> int:
        return len({p.asset for p in self.simulated + self.static})

    def sample(self, t: float, include_static: bool = False) -> Iterator[tuple[Point, Value]]:
        """Yield (point, value) for every simulated point at elapsed time `t`.

        Parameters:
            t: Seconds since simulation start.
            include_static: Also yield static points at their initial value
                (publishers emit them as constant telemetry; servers own their
                live values and must not).
        """
        for point in self.simulated:
            assert point.simulator is not None  # by construction
            yield point, point.simulator.value(t)
        if include_static:
            for point in self.static:
                yield point, initial_value(point.spec)
