"""Deterministic waveform generators for simulated tags.

This file is copied verbatim across the simulator apps (opcua, mqtt, kafka, ...);
keep changes in sync.

Each generator is a pure function of elapsed time plus a per-tag random state,
so runs are reproducible for a given seed and two machines with the same tag
spec diverge only through their seeds and phase offsets.
"""

import math
import random

from models import TagSpec


class TagSimulator:
    """Produces successive values for one tag on one machine.

    Parameters:
        spec: The tag specification (waveform, bounds, period, noise, type).
        seed: Seed for this tag's private random generator. Derive it from the
            global seed, the machine index, and the tag name so every
            machine/tag pair evolves independently but reproducibly.

    Examples:
        >>> sim = TagSimulator(TagSpec(waveform="sine", min=0, max=10, period=60), seed=1)
        >>> isinstance(sim.value(t=0.0), float)
        True
    """

    def __init__(self, spec: TagSpec, seed: int) -> None:
        self._spec = spec
        self._rng = random.Random(seed)
        # Phase offset (fraction of a period) so identical machines don't move in lockstep.
        self._phase = self._rng.uniform(0, spec.period)
        # random_walk keeps state between ticks; start at the initial value or the midpoint.
        self._walk = float(spec.initial) if isinstance(spec.initial, (int, float)) else (spec.min + spec.max) / 2
        # Reflect step: 2% of the range per tick keeps walks visibly moving yet bounded.
        self._walk_step = (spec.max - spec.min) * 0.02

    def value(self, t: float) -> float | int | bool:
        """Compute the tag value at elapsed time `t` seconds.

        Parameters:
            t: Seconds since simulation start.

        Returns:
            The value converted to the tag's declared type.
        """
        spec = self._spec
        raw = self._raw_value(t)

        if spec.type == "bool":
            return bool(raw)

        # Clamp: noise (or a walk step) must not escape the declared bounds.
        raw = min(max(raw, spec.min), spec.max)
        if spec.type == "int":
            return round(raw)
        return raw

    def _raw_value(self, t: float) -> float:
        spec = self._spec
        mid = (spec.min + spec.max) / 2
        amplitude = (spec.max - spec.min) / 2
        cycle_pos = ((t + self._phase) % spec.period) / spec.period

        if spec.waveform == "sine":
            base = mid + amplitude * math.sin(2 * math.pi * cycle_pos)
        elif spec.waveform == "ramp":
            base = spec.min + cycle_pos * (spec.max - spec.min)
        elif spec.waveform == "square":
            if spec.type == "bool":
                return cycle_pos < 0.5
            base = spec.max if cycle_pos < 0.5 else spec.min
        elif spec.waveform == "random":
            if spec.type == "bool":
                return self._rng.random() < 0.5
            base = self._rng.uniform(spec.min, spec.max)
        elif spec.waveform == "random_walk":
            self._walk += self._rng.gauss(0, self._walk_step)
            # Reflect off the bounds so the walk stays in range without sticking to an edge.
            if self._walk > spec.max:
                self._walk = spec.max - (self._walk - spec.max)
            if self._walk < spec.min:
                self._walk = spec.min + (spec.min - self._walk)
            base = self._walk
        else:  # constant
            if spec.type == "bool":
                return bool(spec.initial) if spec.initial is not None else False
            base = float(spec.initial) if isinstance(spec.initial, (int, float)) else mid

        if spec.noise > 0:
            base += self._rng.gauss(0, spec.noise)
        return base
