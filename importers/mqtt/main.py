import asyncio
import contextlib
import json
import ssl
import time
from typing import NamedTuple, Optional

import aiomqtt
from kelvin.application import AssetInfo, KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import ControlChangeMsg, ControlChangeStatus, Message
from kelvin.message.base_messages import ControlChangeStatusPayload, StateEnum
from kelvin.message.msg_type import KMessageType
from pydantic import ValidationError

from settings import Settings

app = KelvinApp()

_DEFAULT_RECONNECT = 5                         # fallback wait when config is invalid
_MAX_QUEUED_COMMANDS = 1000                    # bound the backlog so a broker outage can't grow memory
_REPORT_INTERVAL = 60.0                        # seconds between ingest summaries (only logged on activity)
_COMMAND_MAX_AGE = 30.0                        # drop control changes older than this; a broker outage
                                               # would otherwise flush stale setpoints on reconnect
# Queue carries (monotonic enqueue time, command) so the command loop can drop stale backlog.
_commands: "asyncio.Queue[tuple[float, ControlChangeMsg]]" = asyncio.Queue(maxsize=_MAX_QUEUED_COMMANDS)  # handler -> command loop


class IngestStats:
    """Counters behind the periodic ingest summary; reset every report interval.

    Per-message INFO logging would flood, and silence hides a dead pipeline, so the read
    loop counts and a reporter task logs one line per interval when anything happened."""

    def __init__(self) -> None:
        self.rows = 0
        self.unparseable = 0
        self.topics: set[str] = set()

    def snapshot_and_reset(self) -> tuple[int, int, int]:
        out = (self.rows, self.unparseable, len(self.topics))
        self.rows, self.unparseable, self.topics = 0, 0, set()
        return out


class StreamMapping(NamedTuple):
    asset: str
    stream: str
    msg_type: KMessageType                     # the stream's declared type (drives primitive + publish, incl. icd)
    payload_field: Optional[str]               # dotted path into a JSON payload; None => whole payload


# --- pure helpers (unit-tested) -------------------------------------------------------------

def primitive_name(msg_type: KMessageType) -> str:
    """The declared primitive ('number'|'string'|'boolean'|'object') as a plain string."""
    p = getattr(msg_type, "primitive", None)
    return str(getattr(p, "value", p)) if p is not None else "string"


def resolve(template: str, asset: str, stream: str) -> str:
    """Substitute {asset}/{stream} placeholders (fleet templating); a literal topic is unchanged."""
    return template.replace("{asset}", asset).replace("{stream}", stream)


def extract_field(obj: object, path: str) -> object:
    """Walk a dotted path into a parsed JSON object (dicts only); raise KeyError if absent."""
    value = obj
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def coerce(value: object, primitive: str) -> object:
    if primitive == "number":
        # bool is a subclass of int, so float(True) == 1.0; reject it so a boolean isn't
        # silently ingested as a number.
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"cannot convert {type(value).__name__} to number")
        return float(value)                    # value is a numeric string or already numeric
    if primitive == "boolean":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes", "on")
    if primitive == "object":
        return value if isinstance(value, (dict, list)) else json.loads(str(value))
    return str(value)


def decode_value(raw: object, primitive: str, payload_field: Optional[str]) -> object:
    """Decode an MQTT payload into the stream's value, optionally extracting a JSON field first."""
    text = raw.decode("utf-8").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
    if payload_field is not None:
        return coerce(extract_field(json.loads(text), payload_field), primitive)
    if primitive == "object":
        return json.loads(text)
    return coerce(text, primitive)


def command_payload(value: object) -> str:
    """Serialize a control-change value for an MQTT command publish."""
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def build_topic_map(assets: dict[str, AssetInfo]) -> dict[str, list[StreamMapping]]:
    """Map each (resolved) topic filter to the stream targets that subscribe to it."""
    topic_map: dict[str, list[StreamMapping]] = {}
    for asset_name, asset_info in assets.items():
        for stream_name, sds in asset_info.datastreams.items():
            raw_topic = sds.configuration.get("topic")
            if not raw_topic:
                if not sds.configuration.get("control_topic"):
                    # No topic and no control_topic: this stream can neither ingest nor write back.
                    logger.warning("Stream has neither topic nor control_topic configured; ignoring",
                                   asset=asset_name, stream=stream_name)
                continue  # writeback-only stream (control_topic without topic); handled by build_command_map
            topic = resolve(raw_topic, asset_name, stream_name)
            mapping = StreamMapping(asset_name, stream_name, sds.datastream.type, sds.configuration.get("payload_field"))
            topic_map.setdefault(topic, []).append(mapping)
    return topic_map


def build_command_map(assets: dict[str, AssetInfo]) -> dict[tuple[str, str], str]:
    """Map each (asset, stream) with a control_topic to its (resolved) MQTT command topic."""
    command_map: dict[tuple[str, str], str] = {}
    for asset_name, asset_info in assets.items():
        for stream_name, sds in asset_info.datastreams.items():
            raw = sds.configuration.get("control_topic")
            if raw:
                command_map[(asset_name, stream_name)] = resolve(raw, asset_name, stream_name)
    return command_map


def match_targets(topic: aiomqtt.Topic, topic_map: dict[str, list[StreamMapping]]) -> list[StreamMapping]:
    return [m for topic_filter, ms in topic_map.items() if topic.matches(topic_filter) for m in ms]


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Flatten a (possibly nested) ExceptionGroup into its leaf exceptions."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _flatten_exceptions(sub)]
    return [exc]


# --- runtime --------------------------------------------------------------------------------

@app.on_control_change
async def on_control_change(msg: ControlChangeMsg) -> None:
    """Hand control changes to the command loop via a bounded queue (the read loop owns the MQTT client).
    Registered at import (before connect) so early control changes aren't missed. On backlog (queue full)
    the command is rejected with a failed ack rather than dropped silently; the callback never waits
    on the queue (put_nowait); a full queue is rejected with a failed ack."""
    try:
        _commands.put_nowait((time.monotonic(), msg))
    except asyncio.QueueFull:
        logger.warning("Command queue full; rejecting control change", qsize=_commands.qsize())
        await app.publish(_status(msg, StateEnum.failed, "connector backlog: command queue full"))


def _status(msg: ControlChangeMsg, state: StateEnum, message: str) -> ControlChangeStatus:
    return ControlChangeStatus(
        resource=msg.resource,
        payload=ControlChangeStatusPayload(state=state, message=message, control_change_id=msg.id),
    )


async def _read_loop(client: aiomqtt.Client, topic_map: dict[str, list[StreamMapping]],
                     stats: Optional[IngestStats] = None) -> None:
    stats = stats if stats is not None else IngestStats()   # default: tests drive the loop directly
    async for message in client.messages:
        for m in match_targets(message.topic, topic_map):
            try:
                value = decode_value(message.payload, primitive_name(m.msg_type), m.payload_field)
            except (ValueError, KeyError) as e:
                stats.unparseable += 1
                if stats.unparseable == 1:     # detail once per interval; the summary carries the count
                    logger.warning("Skipping unparseable payload", topic=str(message.topic),
                                   error=str(e), error_type=type(e).__name__)
                continue
            await app.publish(Message(type=m.msg_type, resource=KRNAssetDataStream(m.asset, m.stream), payload=value))
            stats.rows += 1
            stats.topics.add(str(message.topic))
    # `client.messages` ended without raising (broker closed the stream / StopAsyncIteration).
    # Returning here would leave the command and report loops alive while nothing is ingested,
    # so raise to tear down the TaskGroup and drive the outer reconnect loop.
    raise aiomqtt.MqttError("MQTT message stream ended unexpectedly")


async def _report_loop(stats: IngestStats) -> None:
    """Log one liveness line per interval, but only when data (or garbage) actually flowed."""
    while True:
        await asyncio.sleep(_REPORT_INTERVAL)
        rows, unparseable, topics = stats.snapshot_and_reset()
        if rows or unparseable:
            extra = {"unparseable": unparseable} if unparseable else {}
            logger.info("Ingested from MQTT", rows=rows, topics=topics, **extra)


async def _handle_command(client: aiomqtt.Client, command_map: dict[tuple[str, str], str],
                          msg: ControlChangeMsg) -> None:
    """Publish one control change to its MQTT command topic and ack with a ControlChangeStatus."""
    if not isinstance(msg.resource, KRNAssetDataStream):
        logger.warning("Control change resource is not an asset datastream", resource=str(msg.resource))
        await app.publish(_status(msg, StateEnum.failed, "resource is not an asset datastream"))
        return
    key = (msg.resource.asset, msg.resource.data_stream)
    topic = command_map.get(key)
    if topic is None:
        logger.warning("Control change for a stream with no control_topic", asset=key[0], stream=key[1])
        await app.publish(_status(msg, StateEnum.failed, "no control_topic mapped"))
        return
    try:
        # QoS 1: await broker acknowledgment so we only ack `processed` once the broker has the command.
        await client.publish(topic, payload=command_payload(msg.payload.payload), qos=1)
    except (aiomqtt.MqttError, OSError) as e:
        # Ack failed so the platform learns the outcome, then re-raise to tear down and reconnect.
        logger.warning("Failed to publish control command", topic=topic,
                       error=str(e), error_type=type(e).__name__)
        await app.publish(_status(msg, StateEnum.failed, str(e)))
        raise
    logger.info("Published control command to MQTT", topic=topic, asset=key[0], stream=key[1])
    await app.publish(_status(msg, StateEnum.processed, "command published"))


async def _command_loop(client: aiomqtt.Client, command_map: dict[tuple[str, str], str]) -> None:
    while True:
        enqueued_at, msg = await _commands.get()
        age = time.monotonic() - enqueued_at
        try:
            if age > _COMMAND_MAX_AGE:
                # Backlog that built up while disconnected: applying it now would push stale
                # setpoints. Drop it and ack failed so the platform stops waiting on it.
                logger.warning("Dropping stale control change", age_seconds=round(age, 1),
                               resource=str(msg.resource))
                await app.publish(_status(msg, StateEnum.failed, f"stale command dropped after {age:.0f}s"))
                continue
            await _handle_command(client, command_map, msg)
        except asyncio.CancelledError:
            # Teardown (e.g. the read loop failed) cancelled us mid-command or mid stale-drop ack:
            # emit a terminal (failed) ack so the platform doesn't wait on the command forever,
            # then re-raise. Both ack paths get the same best-effort-on-teardown protection.
            with contextlib.suppress(Exception):
                await app.publish(_status(msg, StateEnum.failed, "connector shutting down"))
            raise


async def _consume(settings: Settings) -> None:
    mqtt = settings.mqtt
    a = mqtt.auth
    authed = bool(a.username and a.password)
    async with aiomqtt.Client(
        hostname=mqtt.host,
        port=mqtt.port,
        identifier=mqtt.client_id,
        username=a.username if authed else None,
        password=a.password.get_secret_value() if authed and a.password else None,
        tls_context=ssl.create_default_context() if mqtt.use_tls else None,
    ) as client:
        topic_map = build_topic_map(app.assets)
        command_map = build_command_map(app.assets)
        if not topic_map and not command_map:
            logger.warning("No streams mapped; check per-stream IO configuration (topic/control_topic)")
        elif not topic_map:
            logger.info("No inbound topics mapped; running writeback-only")
        for topic_filter in topic_map:
            await client.subscribe(topic_filter, qos=settings.qos)
        logger.info("Connected to MQTT", host=mqtt.host, port=mqtt.port,
                    topics=len(topic_map), commands=len(command_map))
        # Read loop and control-writeback loop share one client; if either fails the group
        # tears both down and main reconnects.
        stats = IngestStats()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_read_loop(client, topic_map, stats))
            tg.create_task(_command_loop(client, command_map))
            tg.create_task(_report_loop(stats))


async def main() -> None:
    await app.connect()

    while True:
        # Re-read config each iteration so every reconnect starts from the current configuration.
        # (Config/mapping changes still require a redeploy; the platform restarts the workload.)
        try:
            settings = Settings(**app.app_configuration)
        except ValidationError as e:
            logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
            await asyncio.sleep(_DEFAULT_RECONNECT)
            continue

        try:
            await _consume(settings)
        except* (aiomqtt.MqttError, OSError) as eg:
            # Surface the primary (first) leaf with the repo's standard scalar fields, but keep a
            # count plus the remaining leaf types so sibling/nested causes still surface.
            leaves = _flatten_exceptions(eg)
            primary = leaves[0]
            logger.warning("MQTT connection lost; reconnecting",
                           error=str(primary), error_type=type(primary).__name__,
                           error_count=len(leaves),
                           other_error_types=[type(e).__name__ for e in leaves[1:]],
                           wait_seconds=settings.reconnect_interval)
            await asyncio.sleep(settings.reconnect_interval)


if __name__ == "__main__":
    asyncio.run(main())
