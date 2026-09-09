import asyncio
import contextlib
import json
import os
import ssl
import tempfile
from typing import NamedTuple, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError
from kelvin.application import AssetInfo, KelvinApp
from kelvin.krn import KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import ControlChangeMsg, ControlChangeStatus, Message
from kelvin.message.base_messages import ControlChangeStatusPayload, StateEnum
from kelvin.message.msg_type import KMessageType
from pydantic import ValidationError

from settings import Kafka, KafkaTls, Settings

app = KelvinApp()

_DEFAULT_RECONNECT = 5
_MAX_QUEUED_COMMANDS = 1000                        # bound the backlog so a producer outage can't grow memory
_REPORT_INTERVAL = 60.0                            # seconds between ingest summaries (only logged on activity)
_PRODUCER_ERROR_BACKOFF = 1.0                      # pause after a produce failure before serving the next command
_commands: "asyncio.Queue[ControlChangeMsg]" = asyncio.Queue(maxsize=_MAX_QUEUED_COMMANDS)  # handler -> producer loop
_started = asyncio.Event()  # set once _consume's clients + loops are up; lets tests await readiness deterministically


class IngestStats:
    """Counters behind the periodic ingest summary; reset every report interval.

    Per-record INFO logging would flood, and silence hides a dead pipeline, so the read
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
    msg_type: KMessageType                     # declared type drives primitive + publish (incl. icd)
    payload_field: Optional[str]               # dotted path into a JSON record value; None => whole value
    key: Optional[str]                         # only consume records with this key; None => any key


# --- pure helpers (unit-tested) -------------------------------------------------------------

def primitive_name(msg_type: KMessageType) -> str:
    p = getattr(msg_type, "primitive", None)
    return str(getattr(p, "value", p)) if p is not None else "string"


def resolve(template: str, asset: str, stream: str) -> str:
    """Substitute {asset}/{stream} placeholders; a literal value is unchanged."""
    return template.replace("{asset}", asset).replace("{stream}", stream)


def extract_field(obj: object, path: str) -> object:
    value = obj
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def coerce(value: object, primitive: str) -> object:
    if primitive == "number":
        if not isinstance(value, (int, float, str)):
            raise ValueError(f"cannot convert {type(value).__name__} to number")
        return float(value)
    if primitive == "boolean":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes", "on")
    if primitive == "object":
        return value if isinstance(value, (dict, list)) else json.loads(str(value))
    return str(value)


def decode_value(raw: object, primitive: str, payload_field: Optional[str]) -> object:
    text = raw.decode("utf-8").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
    if payload_field is not None:
        return coerce(extract_field(json.loads(text), payload_field), primitive)
    if primitive == "object":
        return json.loads(text)
    return coerce(text, primitive)


def command_payload(value: object) -> str:
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def build_topic_map(assets: dict[str, AssetInfo]) -> dict[str, list[StreamMapping]]:
    """Map each (resolved) Kafka topic to the stream targets consuming it."""
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
            raw_key = sds.configuration.get("key")
            key = resolve(raw_key, asset_name, stream_name) if raw_key else None
            if getattr(sds.datastream.type, "primitive", None) is None:
                # Surface the primitive_name() fallback once here instead of silently per record.
                logger.warning("Datastream type has no usable primitive; values will publish as strings",
                               asset=asset_name, stream=stream_name)
            topic_map.setdefault(topic, []).append(
                StreamMapping(asset_name, stream_name, sds.datastream.type, sds.configuration.get("payload_field"), key)
            )
    return topic_map


def build_command_map(assets: dict[str, AssetInfo]) -> dict[tuple[str, str], str]:
    command_map: dict[tuple[str, str], str] = {}
    for asset_name, asset_info in assets.items():
        for stream_name, sds in asset_info.datastreams.items():
            raw = sds.configuration.get("control_topic")
            if raw:
                command_map[(asset_name, stream_name)] = resolve(raw, asset_name, stream_name)
    return command_map


def match_targets(topic: str, key: Optional[str], topic_map: dict[str, list[StreamMapping]]) -> list[StreamMapping]:
    """Targets on this topic whose optional key filter matches the record key (exact topic, like Kafka)."""
    return [m for m in topic_map.get(topic, []) if m.key is None or m.key == key]


# --- runtime --------------------------------------------------------------------------------

@app.on_control_change
async def on_control_change(msg: ControlChangeMsg) -> None:
    """Hand control changes to the producer loop via a bounded queue (registered at import, before connect).
    On backlog (queue full) the command is rejected with a failed ack rather than dropped silently; the
    callback never waits on the queue (put_nowait). Command validity/expiry is the platform's concern,
    not the connector's."""
    try:
        _commands.put_nowait(msg)
    except asyncio.QueueFull:
        logger.warning("Command queue full; rejecting control change", qsize=_commands.qsize())
        await app.publish(_status(msg, StateEnum.failed, "connector backlog: command queue full"))


def _status(msg: ControlChangeMsg, state: StateEnum, message: str) -> ControlChangeStatus:
    return ControlChangeStatus(
        resource=msg.resource,
        payload=ControlChangeStatusPayload(state=state, message=message, control_change_id=msg.id),
    )


async def _read_loop(consumer: AIOKafkaConsumer, topic_map: dict[str, list[StreamMapping]],
                     stats: Optional[IngestStats] = None) -> None:
    stats = stats if stats is not None else IngestStats()   # default: tests drive the loop directly
    async for record in consumer:
        if record.value is None:               # tombstone
            continue
        # Keys may be binary (non-UTF-8); replace undecodable bytes so the record still flows to
        # any unkeyed mappings instead of crashing the loop.
        key = record.key.decode("utf-8", "replace") if record.key is not None else None
        for m in match_targets(record.topic, key, topic_map):
            try:
                value = decode_value(record.value, primitive_name(m.msg_type), m.payload_field)
            except (ValueError, KeyError) as e:
                stats.unparseable += 1
                if stats.unparseable == 1:     # detail once per interval; the summary carries the count
                    logger.warning("Skipping unparseable record", topic=record.topic,
                                   error=str(e), error_type=type(e).__name__)
                continue
            await app.publish(Message(type=m.msg_type, resource=KRNAssetDataStream(m.asset, m.stream), payload=value))
            stats.rows += 1
            stats.topics.add(record.topic)


async def _report_loop(stats: IngestStats) -> None:
    """Log one liveness line per interval, but only when data (or garbage) actually flowed."""
    while True:
        await asyncio.sleep(_REPORT_INTERVAL)
        rows, unparseable, topic_count = stats.snapshot_and_reset()
        if rows or unparseable:
            extra = {"unparseable": unparseable} if unparseable else {}
            logger.info("Ingested from Kafka", rows=rows, topic_count=topic_count, **extra)


async def _handle_command(producer: Optional[AIOKafkaProducer], command_map: dict[tuple[str, str], str],
                          msg: ControlChangeMsg) -> None:
    """Produce one control change to its Kafka topic (keyed by asset) and ack with ControlChangeStatus.

    `producer` is Optional because the command loop always runs (it is the single ack authority for
    every control change), even in an ingest-only config with no producer. Terminal acks that need no
    Kafka write (bad resource / no mapped control_topic) go back via `app.publish`, so they work with
    producer=None; the send path below is only reached when a control_topic is mapped, which guarantees
    the producer was built."""
    if not isinstance(msg.resource, KRNAssetDataStream):
        logger.warning("Control change resource is not an asset datastream", resource=str(msg.resource))
        await app.publish(_status(msg, StateEnum.failed, "resource is not an asset datastream"))
        return
    key = (msg.resource.asset, msg.resource.data_stream)
    topic = command_map.get(key)
    if topic is None or producer is None:
        # No mapped control_topic (ingest-only stream): this command can't be written back, so ack
        # it `failed` (terminal) rather than leave the platform waiting. producer is None here too.
        logger.warning("Control change for a stream with no control_topic", asset=key[0], stream=key[1])
        await app.publish(_status(msg, StateEnum.failed, "no control_topic mapped"))
        return
    try:
        await producer.send_and_wait(topic, value=command_payload(msg.payload.payload).encode("utf-8"),
                                     key=key[0].encode("utf-8"))
    except (KafkaError, OSError) as e:
        # Ack failed so the platform learns the outcome, then re-raise. The command loop catches
        # this (it does NOT propagate to the consumer), so a transient outbound failure never
        # cancels the healthy read loop.
        logger.warning("Failed to publish control command", topic=topic,
                       error=str(e), error_type=type(e).__name__)
        await app.publish(_status(msg, StateEnum.failed, str(e)))
        raise
    logger.info("Published control command to Kafka", topic=topic, asset=key[0], stream=key[1])
    await app.publish(_status(msg, StateEnum.processed, "command published"))


async def _command_loop(producer: Optional[AIOKafkaProducer], command_map: dict[tuple[str, str], str]) -> None:
    while True:
        msg = await _commands.get()
        try:
            await _handle_command(producer, command_map, msg)
        except asyncio.CancelledError:
            # Teardown (e.g. the read loop failed) cancelled us mid-command: ack the in-flight
            # command as failed so the platform doesn't wait on it forever, then re-raise.
            with contextlib.suppress(Exception):
                await app.publish(_status(msg, StateEnum.failed, "connector shutting down"))
            raise
        except (KafkaError, OSError) as e:
            # Outbound produce failed; _handle_command already acked this command `failed`.
            # Swallow it here so a transient producer error never cancels the shared TaskGroup
            # (which would tear down the healthy consumer and re-deliver inbound data under
            # auto-commit). Back off briefly, then keep serving the queue.
            logger.warning("Producer error; command loop continuing after backoff",
                           error=str(e), error_type=type(e).__name__,
                           backoff_seconds=_PRODUCER_ERROR_BACKOFF)
            await asyncio.sleep(_PRODUCER_ERROR_BACKOFF)


def _build_ssl_context(tls: KafkaTls) -> ssl.SSLContext:
    """Build the client SSL context from config-held PEM material.

    A set ca_cert REPLACES the system trust store (private CA); empty keeps the system bundle.
    A client cert/key pair (mTLS) is loaded through a 0700 temp dir because ssl's
    load_cert_chain only accepts file paths; the files are deleted immediately after loading.
    Shared with the Kafka exporter; keep the two copies in sync.
    """
    ctx = ssl.create_default_context(cadata=tls.ca_cert or None)
    if tls.client_cert and tls.client_key:
        with tempfile.TemporaryDirectory(prefix="kelvin-tls-") as tmp:
            cert_path = os.path.join(tmp, "client-cert.pem")
            key_path = os.path.join(tmp, "client-key.pem")
            with open(cert_path, "w") as f:
                f.write(tls.client_cert)
            with open(key_path, "w") as f:
                f.write(tls.client_key.get_secret_value())
            ctx.load_cert_chain(cert_path, key_path)
    return ctx


async def _client_kwargs(k: Kafka) -> dict:
    """Common AIOKafka consumer/producer kwargs from config (protocol + optional SASL/TLS).

    Shared shape with the Kafka exporter's client_kwargs; keep the two in sync.
    """
    sec = k.security
    kw: dict = {"bootstrap_servers": k.bootstrap_servers, "security_protocol": sec.protocol}
    if sec.protocol in ("SSL", "SASL_SSL"):
        # _build_ssl_context does blocking file I/O (temp dir + write + load_cert_chain); run it
        # in a worker thread so it never stalls the event loop.
        kw["ssl_context"] = await asyncio.to_thread(_build_ssl_context, sec.tls)
    if sec.sasl.mechanism:
        kw["sasl_mechanism"] = sec.sasl.mechanism
        kw["sasl_plain_username"] = sec.sasl.username
        kw["sasl_plain_password"] = sec.sasl.password.get_secret_value() if sec.sasl.password else None
    return kw


async def _consume(settings: Settings) -> None:
    _started.clear()
    topic_map = build_topic_map(app.assets)
    command_map = build_command_map(app.assets)
    if not topic_map and not command_map:
        logger.warning("No streams mapped; check per-stream IO configuration (topic/control_topic)")
    elif not topic_map:
        logger.info("No inbound topics mapped; running writeback-only")
    kw = await _client_kwargs(settings.kafka)

    # Only build the clients we'll actually use: a writeback-only config skips the consumer,
    # an ingest-only config skips the producer. Starting an idle client just burns a broker
    # connection (and a zero-topic consumer never yields anything).
    consumer = (AIOKafkaConsumer(*topic_map.keys(), group_id=settings.kafka.group_id,
                                 auto_offset_reset=settings.kafka.auto_offset_reset, **kw)
                if topic_map else None)
    producer = AIOKafkaProducer(**kw) if command_map else None
    try:
        # Start inside the try so finally always stops whatever started (stop() is safe on a
        # not-fully-started client); a failed producer.start() can't leak the consumer.
        if consumer is not None:
            await consumer.start()
        if producer is not None:
            await producer.start()
        logger.info("Connected to Kafka", brokers=settings.kafka.bootstrap_servers,
                    mapped_topics=len(topic_map), commands=len(command_map))
        stats = IngestStats()
        async with asyncio.TaskGroup() as tg:
            if consumer is not None:
                tg.create_task(_read_loop(consumer, topic_map, stats))
            # The command loop ALWAYS runs: on_control_change enqueues unconditionally, so this loop
            # is the single ack authority for every control change. In an ingest-only config it drains
            # the queue and acks each command `failed` (no control_topic) instead of leaving the
            # platform waiting forever. It needs no producer for those terminal acks.
            tg.create_task(_command_loop(producer, command_map))
            tg.create_task(_report_loop(stats))
            _started.set()   # clients started + loops scheduled; readiness signal for tests
    finally:
        # Stop each client independently so a failing consumer.stop() can't leak the producer.
        for client in (consumer, producer):
            if client is None:
                continue
            try:
                await client.stop()
            except Exception as e:
                logger.warning("Error stopping Kafka client", client=type(client).__name__,
                               error=str(e), error_type=type(e).__name__)


def _leaf_exceptions(eg: BaseException) -> list[BaseException]:
    """Flatten an ExceptionGroup (including nested groups) into its leaf exceptions."""
    if isinstance(eg, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for exc in eg.exceptions:
            leaves.extend(_leaf_exceptions(exc))
        return leaves
    return [eg]


def _reconnect_fields(leaves: list[BaseException]) -> dict:
    """Structured log fields for a reconnect. Use the repo-standard scalar `error`/`error_type` for
    the PRIMARY (first) leaf so the log matches every other error line, and carry a supplementary list
    of the remaining leaf type names so sibling/nested causes still surface."""
    primary = leaves[0]
    return {"error": str(primary), "error_type": type(primary).__name__,
            "error_count": len(leaves),
            "other_error_types": [type(e).__name__ for e in leaves[1:]]}


async def main() -> None:
    await app.connect()

    while True:
        # Re-read config each iteration so every reconnect starts from the current configuration.
        # (Config/mapping changes still require a redeploy; the platform restarts the workload.)
        try:
            settings = Settings(**app.app_configuration)
        except ValidationError as e:
            # errors(include_input=False) keeps raw config values (e.g. a SASL password
            # that failed validation) out of the logs.
            logger.error("Invalid configuration", errors=e.errors(include_url=False, include_input=False))
            await asyncio.sleep(_DEFAULT_RECONNECT)
            continue

        try:
            await _consume(settings)
        except* (KafkaError, OSError) as eg:
            # Log the primary failure with the repo-standard scalar error/error_type fields, and still
            # surface sibling/nested causes (a multi-loop teardown) via error_count + other_error_types.
            leaves = _leaf_exceptions(eg)
            logger.warning("Kafka connection lost; reconnecting",
                           **_reconnect_fields(leaves),
                           wait_seconds=settings.reconnect_interval)
            await asyncio.sleep(settings.reconnect_interval)


if __name__ == "__main__":
    asyncio.run(main())
