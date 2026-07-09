import asyncio
import contextlib
import json
import math
import os
import ssl
import tempfile
from typing import Optional

from aiokafka import AIOKafkaProducer
from aiokafka.errors import (
    AuthenticationFailedError,
    ClusterAuthorizationFailedError,
    IllegalSaslStateError,
    InvalidTopicError,
    TopicAuthorizationFailedError,
    UnknownTopicOrPartitionError,
    UnsupportedSaslMechanismError,
)
from kelvin.logs import logger

from settings import Kafka, KafkaTls
from store import Records, Store

# Deterministic config errors (never self-heal): bad/forbidden credentials, an unsupported or
# misconfigured SASL mechanism, a topic that doesn't exist or isn't writable, bad TLS material.
_CONFIG_ERRORS = (
    AuthenticationFailedError,
    UnsupportedSaslMechanismError,
    IllegalSaslStateError,
    TopicAuthorizationFailedError,
    ClusterAuthorizationFailedError,
    InvalidTopicError,
    UnknownTopicOrPartitionError,
    ssl.SSLError,
)


def build_ssl_context(tls: KafkaTls) -> ssl.SSLContext:
    """Build the client SSL context from config-held PEM material.

    A set ca_cert REPLACES the system trust store (private CA); empty keeps the system bundle.
    A client cert/key pair (mTLS) is loaded through a 0700 temp dir because ssl's
    load_cert_chain only accepts file paths; the files are deleted immediately after loading.
    Shared with the Kafka importer; keep the two copies in sync.
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


def client_kwargs(cfg: Kafka) -> dict:
    """AIOKafka client kwargs from config (brokers + protocol + optional SASL/TLS).

    Shared shape with the Kafka importer's _client_kwargs; keep the two in sync.
    """
    sec = cfg.security
    kw: dict = {"bootstrap_servers": cfg.bootstrap_servers, "client_id": cfg.client_id,
                "security_protocol": sec.protocol}
    if sec.protocol in ("SSL", "SASL_SSL"):
        kw["ssl_context"] = build_ssl_context(sec.tls)
    if sec.sasl.mechanism:
        kw["sasl_mechanism"] = sec.sasl.mechanism
        kw["sasl_plain_username"] = sec.sasl.username
        kw["sasl_plain_password"] = sec.sasl.password.get_secret_value() if sec.sasl.password else None
    return kw


def _payload_value(payload):
    """Coerce a scalar payload to a JSON-serializable value for a Kafka record.

    Non-finite floats (NaN/Inf) become None: json.dumps would otherwise emit literal
    NaN/Infinity, which is invalid JSON that downstream consumers reject.
    """
    if isinstance(payload, float) and not math.isfinite(payload):
        return None
    return payload


class KafkaWriter:
    """Record sink: read a batch from the buffer and produce it to Kafka, one message per row.

    aiokafka is natively async, so its calls are awaited directly (no asyncio.to_thread).
    A single long-lived idempotent producer (acks=all) is reused across batches; a failed
    batch stops the producer and re-raises, so the next attempt starts a fresh one.

    Each row's topic comes from the (asset, datastream) -> topic map main builds from the
    per-stream IO configuration; the message key is "asset/datastream" so one stream's data
    always lands in the same partition. Every batch arrives from the store in chronological
    order, but under upload.order=lifo (or with mixed priorities) *batches* are not oldest-
    first, so per-partition time order across batches only holds with plain FIFO (see README).

    Recovery is at-least-once, not exactly-once: every record's delivery future is awaited
    (so the buffer is only trimmed once the broker acks the whole batch), but a produced-but-
    unacked batch is re-sent on the next tick and duplicates; downstream should tolerate that.
    """

    fmt = None      # record sink: wants dict rows, not a file

    def __init__(self, cfg: Kafka, topics: dict[tuple[str, str], str]):
        self.cfg = cfg
        self.topics = topics
        self._producer: Optional[AIOKafkaProducer] = None

    @staticmethod
    def build_record(row: dict) -> bytes:
        """Build one JSON message value from a buffered row.

        The timestamp is serialized to an ISO-8601 string (JSON can't carry a datetime),
        and a non-finite float payload becomes null.
        """
        return json.dumps({
            "timestamp": row["timestamp"].isoformat(),
            "asset": row["asset"],
            "datastream": row["datastream"],
            "payload": _payload_value(row["payload"]),
        }).encode("utf-8")

    @staticmethod
    def build_key(row: dict) -> bytes:
        """Message key "asset/datastream": one stream -> one partition -> per-stream ordering."""
        return f"{row['asset']}/{row['datastream']}".encode("utf-8")

    async def setup(self) -> None:
        # Start the producer eagerly and fetch metadata for every mapped topic, so problems
        # surface at deploy rather than on the first batch.
        # Failure policy (same convention as the sibling exporters): deterministic config errors
        # (bad credentials, forbidden/nonexistent topic, bad TLS material) raise, so the
        # deployment fails fast and visibly. Transient failures and anything unclassified only
        # warn: the persistent buffer keeps accepting data, and the writer's stop-and-restart-
        # on-failure machinery owns connectivity from here on.
        try:
            producer = await self._ensure_producer()
            for topic in sorted(set(self.topics.values())):
                try:
                    await producer.partitions_for(topic)    # proves the topic exists and is visible
                except UnknownTopicOrPartitionError:
                    # Name the actionable failure: aiokafka only logs its internal
                    # "Topic ... not found in cluster metadata" lines while retrying.
                    logger.error("Kafka topic does not exist; create it or enable broker "
                                 "auto-creation", topic=topic)
                    raise
        except _CONFIG_ERRORS:
            raise                                       # misconfiguration: crash the deployment
        except Exception as e:
            logger.warning("Kafka unreachable at setup; buffering and retrying",
                           brokers=self.cfg.bootstrap_servers,
                           error=str(e), error_type=type(e).__name__)
            await self._close_producer()
            return
        logger.info("Kafka writer ready", brokers=self.cfg.bootstrap_servers,
                    topics=len(set(self.topics.values())))

    async def write_batch(self, store: Store, limit: int) -> Records:
        r = await store.read(limit)
        if r.seqs:
            producer = await self._ensure_producer()
            skipped = 0
            non_finite = 0
            batch_topics: set[str] = set()
            try:
                futures = []
                for row in r.rows:
                    topic = self.topics.get((row["asset"], row["datastream"]))
                    if topic is None:
                        # Mapping removed since the row was buffered (IO config changed on a
                        # redeploy): discard it with the batch; same contract as unmapped streams.
                        skipped += 1
                        continue
                    batch_topics.add(topic)
                    if isinstance(row["payload"], float) and not math.isfinite(row["payload"]):
                        non_finite += 1                 # build_record publishes these as null
                    futures.append(await producer.send(topic, value=self.build_record(row),
                                                       key=self.build_key(row)))
                # Await every delivery future (acks=all): the buffer is only trimmed once the
                # broker acknowledged the whole batch.
                await asyncio.gather(*futures)
            except UnknownTopicOrPartitionError:
                logger.warning("Kafka topic does not exist; create it or enable broker "
                               "auto-creation", topics=sorted(batch_topics))
                await self._close_producer()    # drop the producer so the next attempt restarts clean
                raise
            except Exception:
                await self._close_producer()    # drop the producer so the next attempt restarts clean
                raise
            if skipped:
                logger.warning("Discarded rows for streams no longer mapped to a topic",
                               rows=skipped)
            extra = {"non_finite": non_finite} if non_finite else {}
            logger.info("Published to Kafka", rows=r.n_rows - skipped, backlog=r.backlog,
                        topics=len(batch_topics), brokers=self.cfg.bootstrap_servers, **extra)
        return r

    async def _ensure_producer(self) -> AIOKafkaProducer:
        if self._producer is None:
            # acks="all" + idempotence: the broker de-duplicates producer-level retries, and a
            # send only resolves once the full ISR has the record.
            producer = AIOKafkaProducer(acks="all", enable_idempotence=True,
                                        **client_kwargs(self.cfg))
            try:
                await producer.start()
            except Exception:
                # stop() is safe on a not-fully-started client; never leak its connections.
                with contextlib.suppress(Exception):
                    await producer.stop()
                raise
            self._producer = producer
            logger.info("Kafka producer started", brokers=self.cfg.bootstrap_servers)
        return self._producer

    async def _close_producer(self) -> None:
        if self._producer is not None:
            producer, self._producer = self._producer, None
            try:
                await producer.stop()
            except Exception:
                logger.warning("Failed to stop stale Kafka producer")

    async def teardown(self) -> None:
        await self._close_producer()
