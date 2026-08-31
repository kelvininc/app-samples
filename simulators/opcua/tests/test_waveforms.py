"""Unit tests for the waveform generators."""

import pytest

from models import TagSpec
from waveforms import TagSimulator


# ====================================================
# Fixtures
# ====================================================

def make_sim(seed: int = 1, **spec_kwargs: object) -> TagSimulator:
    """Create a TagSimulator with the given spec overrides.

    Returns:
        TagSimulator: a simulator seeded deterministically.
    """
    return TagSimulator(TagSpec(**spec_kwargs), seed=seed)


# ====================================================
# Test Cases: bounds and types
# ====================================================

@pytest.mark.parametrize("waveform", ["sine", "ramp", "square", "random_walk", "random"])
def test_values_stay_within_bounds(waveform: str) -> None:
    """Test that every waveform, even with noise, never escapes [min, max]."""
    sim = make_sim(waveform=waveform, min=10.0, max=20.0, period=30, noise=5.0)
    for t in range(0, 600):
        value = sim.value(float(t))
        assert 10.0 <= value <= 20.0


def test_int_type_returns_ints() -> None:
    """Test that int tags produce int values, not floats."""
    sim = make_sim(waveform="sine", type="int", min=0, max=100, period=60)
    assert all(isinstance(sim.value(float(t)), int) for t in range(60))


def test_bool_square_alternates() -> None:
    """Test that a bool square wave produces both True and False over a full period."""
    sim = make_sim(waveform="square", type="bool", period=10)
    values = {sim.value(float(t)) for t in range(10)}
    assert values == {True, False}


def test_constant_holds_initial_value() -> None:
    """Test that a constant tag always returns its initial value."""
    sim = make_sim(waveform="constant", initial=42.5, min=0, max=100)
    assert sim.value(0.0) == 42.5
    assert sim.value(1000.0) == 42.5


def test_constant_without_initial_uses_midpoint() -> None:
    """Test that a constant tag with no initial value settles on the range midpoint."""
    sim = make_sim(waveform="constant", min=10, max=30)
    assert sim.value(0.0) == 20.0


# ====================================================
# Test Cases: determinism and divergence
# ====================================================

def test_same_seed_reproduces_the_same_series() -> None:
    """Test that two simulators with identical spec and seed produce identical values."""
    a = make_sim(seed=7, waveform="random_walk", min=0, max=100)
    b = make_sim(seed=7, waveform="random_walk", min=0, max=100)
    assert [a.value(float(t)) for t in range(50)] == [b.value(float(t)) for t in range(50)]


def test_different_seeds_diverge() -> None:
    """Test that two machines with the same tag spec but different seeds produce different series.

    This is the property that keeps identical machines from moving in lockstep.
    """
    a = make_sim(seed=1, waveform="sine", min=0, max=100, period=60)
    b = make_sim(seed=2, waveform="sine", min=0, max=100, period=60)
    series_a = [a.value(float(t)) for t in range(30)]
    series_b = [b.value(float(t)) for t in range(30)]
    assert series_a != series_b


def test_sine_moves_over_time() -> None:
    """Test that a sine tag actually changes value across a period."""
    sim = make_sim(waveform="sine", min=0, max=100, period=20)
    values = {round(sim.value(float(t)), 3) for t in range(20)}
    # A 20-sample sine sweep must produce many distinct values, not a flat line.
    assert len(values) > 10


def test_random_walk_reflects_off_bounds() -> None:
    """Test that a narrow-range random walk stays bounded instead of sticking or escaping.

    Edge case: walk step relative to a tiny range makes boundary hits frequent.
    """
    sim = make_sim(waveform="random_walk", min=0.0, max=1.0)
    values = [sim.value(float(t)) for t in range(1000)]
    assert all(0.0 <= v <= 1.0 for v in values)
    # It must keep moving, not converge onto an edge.
    assert len({round(v, 6) for v in values[-100:]}) > 1
