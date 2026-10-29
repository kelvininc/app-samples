"""Verify decorated Kafka exporting through the Kelvin testing harness."""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import main
import pytest
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError
from kelvin.krn import KRNAssetDataStream
from kelvin.message import Number, RuntimeManifest
from kelvin.testing import KelvinAppTest, ManifestBuilder

MANIFEST = Path(__file__).resolve().parents[1] / "app.yaml"


def build_manifest(topic: str = "demo.motor.temperature") -> RuntimeManifest:
    """Map a numeric input to the external topic for one test Asset."""
    return (
        ManifestBuilder.from_app_yaml(MANIFEST)
        .add_asset("motor-01")
        .add_input("motor_temperature", "number", configuration={"topic": topic})
        .build()
    )


@pytest.fixture
def producer(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace only the external producer boundary; keep the SDK lifecycle real."""
    client = AsyncMock()
    monkeypatch.setattr(main, "AIOKafkaProducer", lambda **kwargs: client)
    return client


class TestKafkaLifecycle:
    """The SDK starts and closes the external producer through decorated callbacks."""

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, producer: AsyncMock) -> None:
        """Startup precedes stream processing, and disconnect closes the producer."""
        async with KelvinAppTest(main.app, manifest=build_manifest()):
            producer.start.assert_awaited_once()
            assert main.producer is producer
        producer.stop.assert_awaited_once()
        assert main.producer is None

    @pytest.mark.asyncio
    async def test_failed_start_closes_partial_producer(
        self, producer: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A startup failure releases the partially opened client and propagates."""
        monkeypatch.setattr(
            main,
            "app",
            SimpleNamespace(
                app_configuration={"bootstrap_servers": "broker.example.com:9092"}
            ),
        )
        producer.start.side_effect = KafkaConnectionError("Broker unreachable")
        with pytest.raises(KafkaConnectionError, match="Broker unreachable"):
            await main.connect_kafka()
        producer.stop.assert_awaited_once()
        assert main.producer is None

    @pytest.mark.asyncio
    async def test_cancelled_start_closes_partial_producer(
        self, producer: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Canceling startup closes the client and clears its reference."""
        monkeypatch.setattr(
            main,
            "app",
            SimpleNamespace(
                app_configuration={"bootstrap_servers": "broker.example.com:9092"}
            ),
        )
        starting = asyncio.Event()

        async def blocked_start() -> None:
            """Wait until the test cancels the opening broker connection."""
            starting.set()
            await asyncio.Event().wait()

        producer.start.side_effect = blocked_start
        task = asyncio.create_task(main.connect_kafka())
        await starting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        producer.stop.assert_awaited_once()
        assert main.producer is None


class TestExportReading:
    """Validate record contents, numeric values, mapping, and invalid payload behavior."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [24.5, 0.0, -10.0])
    async def test_sends_mapped_record(self, producer: AsyncMock, value: float) -> None:
        """A numeric input becomes one UTC timestamped record with the mapped topic."""
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        async with KelvinAppTest(main.app, manifest=build_manifest()) as harness:
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("motor-01", "motor_temperature"),
                    payload=value,
                    timestamp=timestamp,
                )
            )
            await harness.run_until_idle()
        producer.send_and_wait.assert_awaited_once()
        topic, body = producer.send_and_wait.call_args.args
        assert topic == "demo.motor.temperature"
        assert json.loads(body) == {
            "asset": "motor-01",
            "datastream": "motor_temperature",
            "timestamp": timestamp.isoformat(),
            "payload": value,
        }
        assert (
            producer.send_and_wait.call_args.kwargs["key"]
            == b"motor-01/motor_temperature"
        )

    @pytest.mark.asyncio
    async def test_alias_preserves_physical_datastream_in_record(
        self, producer: AsyncMock
    ) -> None:
        """The SDK input alias selects a mapping, and the record keeps the physical name."""
        manifest = build_manifest()
        manifest.payload.resources[0].datastreams[
            "motor_temperature"
        ].map_to = "temperature_input"
        async with KelvinAppTest(main.app, manifest=manifest) as harness:
            await harness.publish(
                Number(
                    resource=KRNAssetDataStream("motor-01", "temperature_input"),
                    payload=25.0,
                )
            )
            await harness.run_until_idle()
        producer.send_and_wait.assert_awaited_once()
        body = json.loads(producer.send_and_wait.call_args.args[1])
        assert body["datastream"] == "motor_temperature"
        assert (
            producer.send_and_wait.call_args.kwargs["key"]
            == b"motor-01/motor_temperature"
        )

    @pytest.mark.asyncio
    async def test_send_failure_does_not_stop_later_readings(
        self, producer: AsyncMock
    ) -> None:
        """The live stream continues after logging an external send failure."""
        producer.send_and_wait.side_effect = [
            KafkaConnectionError("Broker disconnected"),
            None,
        ]
        async with KelvinAppTest(main.app, manifest=build_manifest()) as harness:
            for value in [24.5, 25.0]:
                await harness.publish(
                    Number(
                        resource=KRNAssetDataStream("motor-01", "motor_temperature"),
                        payload=value,
                    )
                )
                await harness.run_until_idle()
        assert producer.send_and_wait.await_count == 2
        assert [
            json.loads(call.args[1])["payload"]
            for call in producer.send_and_wait.call_args_list
        ] == [24.5, 25.0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_external_broker_receives_record() -> None:
    """A real Kafka client receives a record sent by the decorator-registered handler."""
    bootstrap = os.environ["KAFKA_TEST_BOOTSTRAP_SERVERS"]
    topic = "docs-test-" + uuid.uuid4().hex
    builder = (
        ManifestBuilder.from_app_yaml(MANIFEST)
        .add_asset("motor-01")
        .add_input("motor_temperature", "number", configuration={"topic": topic})
    )
    builder.set_configuration({"bootstrap_servers": bootstrap})
    async with KelvinAppTest(main.app, manifest=builder.build()) as harness:
        await harness.publish(
            Number(
                resource=KRNAssetDataStream("motor-01", "motor_temperature"),
                payload=24.5,
            )
        )
        await harness.run_until_idle(timeout=20)
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=bootstrap,
            auto_offset_reset="earliest",
            group_id=None,
        )
        await consumer.start()
        try:
            record = await asyncio.wait_for(consumer.getone(), timeout=30)
            assert json.loads(record.value)["payload"] == 24.5
            assert record.key == b"motor-01/motor_temperature"
        finally:
            await consumer.stop()
