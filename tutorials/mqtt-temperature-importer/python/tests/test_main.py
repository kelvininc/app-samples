"""Verify decoding and publishing to runtime-mapped Data Streams."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import aiomqtt
import main
import pytest
from kelvin.testing import KelvinAppTest, ManifestBuilder

ORIGINAL_INGEST = main.ingest
PROJECT_MANIFEST = Path(__file__).resolve().parents[1] / "app.yaml"


@pytest.fixture(autouse=True)
def isolate_external_reader(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Keep unit tests offline; integration tests run the registered MQTT task."""
    if request.node.get_closest_marker("integration"):
        return

    async def idle_reader(application: main.KelvinApp) -> None:
        """Wait for SDK cancellation without opening a broker connection."""
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "ingest", idle_reader)


@pytest.mark.parametrize(
    "payload,expected", [(b"24.5", 24.5), (b"-10", -10.0), (b"0", 0.0)]
)
def test_decode_temperature(payload: bytes, expected: float) -> None:
    """Numeric MQTT readings retain their values, including zero and negatives."""
    assert main.decode_temperature(payload) == expected


@pytest.mark.parametrize(
    "payload", [b"", b"not-a-number", b"nan", b"inf", b"-inf", b"\xff"]
)
def test_reject_invalid_temperature(payload: bytes) -> None:
    """Reject nonnumeric, nonfinite, and incorrectly encoded readings."""
    with pytest.raises(ValueError, match="Temperature must be a finite number"):
        main.decode_temperature(payload)


async def test_publish_to_all_matching_mapped_streams() -> None:
    """A topic feeds all matching mapped streams and leaves other mappings untouched."""
    manifest = (
        ManifestBuilder.from_app_yaml(PROJECT_MANIFEST)
        .add_asset("demo-motor")
        .add_input(
            "temperature", "number", configuration={"topic": "demo/motor/temperature"}
        )
        .add_input(
            "temperature_copy",
            "number",
            configuration={"topic": "demo/motor/temperature"},
        )
        .add_input("pressure", "number", configuration={"topic": "demo/motor/pressure"})
        .build()
    )
    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        await main.publish_reading(
            main.app,
            main.topic_targets(main.app),
            aiomqtt.Topic("demo/motor/temperature"),
            b"24.5",
        )
        assert {output.resource.data_stream for output in harness.outputs} == {
            "temperature",
            "temperature_copy",
        }
        assert all(
            output.resource.asset == "demo-motor" and output.payload == 24.5
            for output in harness.outputs
        )
        assert all(
            output.type.primitive.value == "number" for output in harness.outputs
        )


async def test_invalid_and_unmapped_readings_publish_nothing() -> None:
    """Malformed readings and topics with no mapping never become Kelvin messages."""
    manifest = (
        ManifestBuilder.from_app_yaml(PROJECT_MANIFEST)
        .add_asset("demo-motor")
        .add_input(
            "temperature", "number", configuration={"topic": "demo/motor/temperature"}
        )
        .build()
    )
    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        targets = main.topic_targets(main.app)
        await main.publish_reading(
            main.app, targets, aiomqtt.Topic("demo/motor/temperature"), b"nan"
        )
        await main.publish_reading(
            main.app, targets, aiomqtt.Topic("demo/other/temperature"), b"24.5"
        )
        assert harness.outputs == []


@pytest.mark.integration
async def test_external_broker_to_kelvin_publish() -> None:
    """A real broker delivers a retained reading that becomes a mapped Kelvin message."""
    host = os.environ["MQTT_TEST_HOST"]
    port = int(os.environ["MQTT_TEST_PORT"])
    topic = "docs-test/" + uuid.uuid4().hex
    manifest = (
        ManifestBuilder.from_app_yaml(PROJECT_MANIFEST)
        .add_asset("demo-motor")
        .add_input("temperature", "number", configuration={"topic": topic})
        .set_configuration({"mqtt_host": host, "mqtt_port": port})
        .build()
    )
    async with aiomqtt.Client(hostname=host, port=port) as sensor:
        await sensor.publish(topic, "24.5", retain=True, qos=1)
        try:
            async with KelvinAppTest(main.app, manifest=manifest) as harness:

                async def wait_for_reading() -> None:
                    """Wait for the decorator-registered reader to publish the broker value."""
                    while not harness.outputs:
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(wait_for_reading(), timeout=10)
                assert len(harness.outputs) == 1
                assert harness.outputs[0].payload == 24.5
                assert harness.outputs[0].resource.asset == "demo-motor"
                assert harness.outputs[0].resource.data_stream == "temperature"
        finally:
            await sensor.publish(topic, b"", retain=True, qos=1)


async def test_sensor_publishes_sequence_to_external_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The separate sensor sends the documented topic and numeric sequence to MQTT."""
    from unittest.mock import AsyncMock

    import simulate_sensor

    client = AsyncMock()
    client.__aenter__.return_value = client

    def factory(*, hostname: str, port: int) -> AsyncMock:
        """Verify the sensor opens the specified external broker connection."""
        assert hostname == "broker.example.com"
        assert port == 1883
        return client

    monkeypatch.setattr(simulate_sensor.aiomqtt, "Client", factory)
    count = 0

    async def end_after_three_readings(seconds: float) -> None:
        """End the infinite simulator after observing three one-second intervals."""
        nonlocal count
        assert seconds == 1
        count += 1
        if count == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(simulate_sensor.asyncio, "sleep", end_after_three_readings)
    with pytest.raises(asyncio.CancelledError):
        await simulate_sensor.simulate("broker.example.com", 1883)
    assert [call.args for call in client.publish.call_args_list] == [
        ("demo/motor/temperature", "24.5"),
        ("demo/motor/temperature", "25.0"),
        ("demo/motor/temperature", "25.5"),
    ]


async def test_routing_uses_actual_datastream_name_when_alias_differs() -> None:
    """Publish to the physical Data Stream when the runtime provides an I/O alias."""
    manifest = (
        ManifestBuilder.from_app_yaml(PROJECT_MANIFEST)
        .add_asset("demo-motor")
        .add_input(
            "motor_temperature",
            "number",
            configuration={"topic": "demo/motor/temperature"},
        )
        .build()
    )
    manifest.payload.resources[0].datastreams[
        "motor_temperature"
    ].map_to = "sensor_input"
    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        assert "sensor_input" in main.app.assets["demo-motor"].datastreams
        await main.publish_reading(
            main.app,
            main.topic_targets(main.app),
            aiomqtt.Topic("demo/motor/temperature"),
            b"24.5",
        )
        assert len(harness.outputs) == 1
        assert harness.outputs[0].resource.data_stream == "motor_temperature"
        assert harness.outputs[0].payload == 24.5


async def test_sdk_starts_reconnects_and_cancels_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registered task retries a broker failure and exits during SDK disconnect."""
    reading = asyncio.Event()
    cancelled = asyncio.Event()
    attempts = 0
    delays: list[float] = []

    async def broker_reader(application: main.KelvinApp) -> None:
        """Fail the first connection, then wait until the SDK cancels ingestion."""
        nonlocal attempts
        attempts += 1
        assert application.is_connected
        if attempts == 1:
            raise aiomqtt.MqttError("Broker disconnected")
        reading.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def retry_delay(seconds: float) -> None:
        """Record the reconnect interval without a real-time delay."""
        delays.append(seconds)

    monkeypatch.setattr(main, "ingest", broker_reader)
    monkeypatch.setattr(main.asyncio, "sleep", retry_delay)
    manifest = ManifestBuilder.from_app_yaml(PROJECT_MANIFEST).build()
    async with KelvinAppTest(main.app, manifest=manifest):
        await asyncio.wait_for(reading.wait(), timeout=5)
        assert attempts == 2
        assert delays == [5]
        assert not cancelled.is_set()
    assert cancelled.is_set()
    assert not main.app.is_connected


async def test_invalid_subscription_leaves_valid_mapping_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an invalid MQTT filter while delivering a valid mapped reading."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.__aenter__.return_value = client

    async def subscribe(topic: str) -> None:
        """Model Paho's invalid-subscription exception at the client boundary."""
        if topic == "demo/#/temperature":
            raise ValueError("Invalid subscription filter")

    async def messages() -> AsyncIterator[SimpleNamespace]:
        """Deliver a numeric reading from the valid MQTT topic."""
        yield SimpleNamespace(
            topic=aiomqtt.Topic("demo/motor/temperature"), payload=b"24.5"
        )

    client.subscribe.side_effect = subscribe
    client.messages = messages()
    monkeypatch.setattr(main.aiomqtt, "Client", lambda **kwargs: client)
    manifest = (
        ManifestBuilder.from_app_yaml(PROJECT_MANIFEST)
        .add_asset("demo-motor")
        .add_input("broken", "number", configuration={"topic": "demo/#/temperature"})
        .add_input(
            "temperature", "number", configuration={"topic": "demo/motor/temperature"}
        )
        .build()
    )
    async with KelvinAppTest(main.app, manifest=manifest) as harness:
        with pytest.raises(aiomqtt.MqttError, match="MQTT message stream ended"):
            await ORIGINAL_INGEST(main.app)
        assert len(harness.outputs) == 1
        assert harness.outputs[0].resource.data_stream == "temperature"
        assert harness.outputs[0].payload == 24.5
