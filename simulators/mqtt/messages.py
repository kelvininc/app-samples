"""Topic/key rendering and payload encoding, shared by the publisher simulators.

This file is copied verbatim across the publisher simulator apps (mqtt, kafka);
keep changes in sync. It turns fleet readings into ready-to-send messages
according to the `payload` / `timestamp` / topic-template configuration.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional

from fleet import Point, Value

_TS_KEY = "timestamp"


@dataclass(frozen=True)
class Message:
    """A ready-to-send message.

    Attributes:
        topic: Rendered topic.
        key: Rendered record key, or None (MQTT, or no key template).
        payload: Encoded body.
    """

    topic: str
    key: Optional[str]
    payload: bytes


def _render(template: str, asset: str, tag: Optional[str]) -> str:
    out = template.replace("{asset}", asset)
    if tag is not None:
        out = out.replace("{tag}", tag)
    return out


def _timestamp(mode: str, now: datetime) -> Optional[str | int]:
    if mode == "iso":
        return now.isoformat()
    if mode == "epoch_ms":
        return int(now.timestamp() * 1000)
    return None  # "none"


def _with_timestamp(body: dict[str, object], mode: str, now: datetime) -> dict[str, object]:
    ts = _timestamp(mode, now)
    if ts is not None:
        body[_TS_KEY] = ts
    return body


def build_messages(
    readings: list[tuple[Point, Value]],
    payload: str,
    timestamp_mode: str,
    topic_template: str,
    now: datetime,
    key_template: Optional[str] = None,
) -> Iterator[Message]:
    """Turn one tick's readings into messages.

    Parameters:
        readings: (point, value) pairs from `Fleet.sample(t, include_static=True)`.
        payload: "raw" | "json" | "json_bundle".
        timestamp_mode: "iso" | "epoch_ms" | "none".
        topic_template: Topic with {asset}/{tag} placeholders.
        now: Timestamp for this tick (UTC).
        key_template: Optional record-key template (Kafka); None yields key=None.

    Yields:
        One Message per tag (raw/json) or per asset (json_bundle).
    """
    if payload == "json_bundle":
        yield from _build_bundled(readings, timestamp_mode, topic_template, key_template, now)
        return

    for point, value in readings:
        topic = _render(topic_template, point.asset, point.tag)
        key = _render(key_template, point.asset, point.tag) if key_template else None
        if payload == "raw":
            body = json.dumps(value).encode()
        else:  # json
            body = json.dumps(_with_timestamp({"value": value}, timestamp_mode, now)).encode()
        yield Message(topic=topic, key=key, payload=body)


def _build_bundled(
    readings: list[tuple[Point, Value]],
    timestamp_mode: str,
    topic_template: str,
    key_template: Optional[str],
    now: datetime,
) -> Iterator[Message]:
    # One message per asset carrying all its tags; preserve first-seen asset order.
    grouped: dict[str, dict[str, object]] = {}
    for point, value in readings:
        grouped.setdefault(point.asset, {})[point.tag] = value
    for asset, tags in grouped.items():
        topic = _render(topic_template, asset, None)
        key = _render(key_template, asset, None) if key_template else None
        body = json.dumps(_with_timestamp(dict(tags), timestamp_mode, now)).encode()
        yield Message(topic=topic, key=key, payload=body)
