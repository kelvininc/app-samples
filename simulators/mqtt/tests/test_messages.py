"""Unit tests for topic/key rendering and payload encoding (shared publisher module)."""

import json
from datetime import datetime, timezone

from fleet import Fleet
from messages import build_messages
from models import AssetGroup, TagSpec

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)


def make_fleet() -> Fleet:
    """Two assets, one moving tag and one writable setpoint each.

    Returns:
        Fleet: for message-building tests.
    """
    group = AssetGroup(
        name="BeamPump",
        count=2,
        tags={
            "spm": TagSpec(waveform="constant", initial=7.0),
            "spm_setpoint": TagSpec(writable=True, initial=8.0),
        },
    )
    return Fleet([group], seed=42)


def readings() -> list:
    return list(make_fleet().sample(0.0, include_static=True))


# ====================================================
# Test Cases: raw
# ====================================================

def test_raw_payload_is_bare_scalar() -> None:
    """Test that raw payloads carry just the JSON scalar, no envelope."""
    msgs = list(build_messages(readings(), "raw", "iso", "sim/{asset}/{tag}", NOW))
    spm = next(m for m in msgs if m.topic == "sim/BeamPump01/spm")
    assert spm.payload == b"7.0"


def test_topic_placeholders_expand_per_tag() -> None:
    """Test that {asset} and {tag} expand into one topic per point."""
    msgs = list(build_messages(readings(), "raw", "none", "sim/{asset}/{tag}", NOW))
    topics = {m.topic for m in msgs}
    assert "sim/BeamPump01/spm" in topics
    assert "sim/BeamPump02/spm_setpoint" in topics


# ====================================================
# Test Cases: json
# ====================================================

def test_json_payload_wraps_value_and_timestamp() -> None:
    """Test that json payloads carry value plus an ISO timestamp."""
    msgs = list(build_messages(readings(), "json", "iso", "sim/{asset}/{tag}", NOW))
    body = json.loads(next(m for m in msgs if m.topic == "sim/BeamPump01/spm").payload)
    assert body == {"value": 7.0, "timestamp": "2026-07-09T12:00:00+00:00"}


def test_timestamp_epoch_ms() -> None:
    """Test that epoch_ms mode emits an integer millisecond timestamp."""
    msgs = list(build_messages(readings(), "json", "epoch_ms", "sim/{asset}/{tag}", NOW))
    body = json.loads(next(iter(msgs)).payload)
    assert body["timestamp"] == int(NOW.timestamp() * 1000)


def test_timestamp_none_omits_field() -> None:
    """Test that timestamp=none produces a bare {"value": ...} body."""
    msgs = list(build_messages(readings(), "json", "none", "sim/{asset}/{tag}", NOW))
    body = json.loads(next(iter(msgs)).payload)
    assert body == {"value": 7.0}


# ====================================================
# Test Cases: json_bundle
# ====================================================

def test_bundle_emits_one_message_per_asset_with_all_tags() -> None:
    """Test that json_bundle groups every tag of an asset into a single message."""
    msgs = list(build_messages(readings(), "json_bundle", "iso", "sim/{asset}", NOW))
    assert {m.topic for m in msgs} == {"sim/BeamPump01", "sim/BeamPump02"}
    body = json.loads(next(m for m in msgs if m.topic == "sim/BeamPump01").payload)
    assert body["spm"] == 7.0
    assert body["spm_setpoint"] == 8.0
    assert body["timestamp"] == "2026-07-09T12:00:00+00:00"


# ====================================================
# Test Cases: key template (Kafka)
# ====================================================

def test_key_template_renders_when_provided() -> None:
    """Test that a key template produces a per-tag record key."""
    msgs = list(build_messages(readings(), "json", "iso", "sim.{asset}", NOW, key_template="{tag}"))
    spm = next(m for m in msgs if m.key == "spm")
    assert spm.topic == "sim.BeamPump01"


def test_key_is_none_without_template() -> None:
    """Test that omitting the key template leaves keys unset (MQTT)."""
    msgs = list(build_messages(readings(), "json", "iso", "sim/{asset}/{tag}", NOW))
    assert all(m.key is None for m in msgs)
