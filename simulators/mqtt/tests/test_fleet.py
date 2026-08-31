"""Unit tests for fleet expansion and sampling."""

from fleet import Fleet, initial_value
from models import AssetGroup, TagSpec


def make_fleet(count: int = 2) -> Fleet:
    """Create a two-tag fleet (one simulated, one writable) for testing.

    Returns:
        Fleet: expanded from a single asset group.
    """
    group = AssetGroup(
        name="BeamPump",
        count=count,
        tags={
            "spm": TagSpec(waveform="sine", min=6, max=12, period=60),
            "spm_setpoint": TagSpec(writable=True, initial=8.0),
        },
    )
    return Fleet([group], seed=42)


# ====================================================
# Test Cases: expansion
# ====================================================

def test_fleet_expands_count_into_numbered_assets() -> None:
    """Test that count=3 produces BeamPump01..03 with the full tag set each."""
    fleet = make_fleet(count=3)
    assert {p.asset for p in fleet.simulated} == {"BeamPump01", "BeamPump02", "BeamPump03"}
    assert fleet.asset_count == 3


def test_writable_tags_become_static_points() -> None:
    """Test that writable tags land in `static`, not in the simulated set."""
    fleet = make_fleet()
    assert {p.tag for p in fleet.simulated} == {"spm"}
    assert {p.tag for p in fleet.static} == {"spm_setpoint"}
    assert all(p.simulator is None for p in fleet.static)


def test_point_id_is_lowercase_asset_dot_tag() -> None:
    """Test the stable point id format used for NodeIds and record keys."""
    fleet = make_fleet(count=1)
    assert fleet.simulated[0].point_id == "beampump01.spm"


# ====================================================
# Test Cases: sampling
# ====================================================

def test_sample_yields_only_simulated_points_by_default() -> None:
    """Test that servers (which own static values) get no static readings."""
    fleet = make_fleet()
    tags = {p.tag for p, _ in fleet.sample(0.0)}
    assert tags == {"spm"}


def test_sample_with_static_includes_initial_values() -> None:
    """Test that publishers get static points at their configured initial value."""
    fleet = make_fleet()
    readings = {(p.asset, p.tag): v for p, v in fleet.sample(0.0, include_static=True)}
    assert readings[("BeamPump01", "spm_setpoint")] == 8.0


def test_assets_do_not_move_in_lockstep() -> None:
    """Test that two instances of the same asset produce different series."""
    fleet = make_fleet()
    series: dict[str, list[float]] = {"BeamPump01": [], "BeamPump02": []}
    for t in range(30):
        for point, value in fleet.sample(float(t)):
            series[point.asset].append(float(value))
    assert series["BeamPump01"] != series["BeamPump02"]


def test_initial_value_defaults_to_midpoint() -> None:
    """Test that a spec without an initial value starts at the range midpoint."""
    assert initial_value(TagSpec(min=10, max=30)) == 20.0
