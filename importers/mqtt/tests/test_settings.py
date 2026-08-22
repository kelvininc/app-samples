"""Unit tests for the MQTT connector Settings model."""
import pytest
from pydantic import ValidationError

from settings import Settings


def test_defaults() -> None:
    """All fields have working defaults (the public-broker sample runs out of the box)."""
    s = Settings()
    assert s.mqtt.host == "test.mosquitto.org" and s.mqtt.port == 1883
    assert s.mqtt.client_id == "kelvin-mqtt-importer" and s.reconnect_interval == 5
    assert s.mqtt.use_tls is False
    assert s.mqtt.auth.username is None and s.mqtt.auth.password is None
    assert s.qos == 0


def test_nested_override() -> None:
    s = Settings(mqtt={"host": "broker.internal", "port": 8883, "use_tls": True}, reconnect_interval=10)
    assert s.mqtt.host == "broker.internal" and s.mqtt.port == 8883
    assert s.mqtt.use_tls is True and s.reconnect_interval == 10


class TestAuth:
    def test_accepts_and_masks_password(self) -> None:
        s = Settings(mqtt={"auth": {"username": "u", "password": "shhh"}})
        assert s.mqtt.auth.password.get_secret_value() == "shhh"
        assert "shhh" not in repr(s.mqtt.auth)

    @pytest.mark.parametrize("partial", [{"username": "u"}, {"password": "p"}])
    def test_rejects_one_field_without_the_other(self, partial: dict) -> None:
        with pytest.raises(ValidationError, match="both username and password"):
            Settings(mqtt={"auth": partial})

    def test_unresolved_secret_password_treated_as_unset(self) -> None:
        with pytest.raises(ValidationError, match="both username and password"):
            Settings(mqtt={"auth": {"username": "u", "password": "<% secrets.mqtt-password %>"}})


@pytest.mark.parametrize("blank", ["", "   "])
def test_rejects_blank_host(blank: str) -> None:
    with pytest.raises(ValidationError):
        Settings(mqtt={"host": blank})


@pytest.mark.parametrize("bad_port", [0, 70000])
def test_rejects_out_of_range_port(bad_port: int) -> None:
    with pytest.raises(ValidationError):
        Settings(mqtt={"port": bad_port})


@pytest.mark.parametrize("bad", [0, -1, 1000])
def test_rejects_out_of_range_reconnect_interval(bad: int) -> None:
    with pytest.raises(ValidationError):
        Settings(reconnect_interval=bad)


@pytest.mark.parametrize("good", [0, 1, 2])
def test_accepts_valid_qos(good: int) -> None:
    assert Settings(qos=good).qos == good


@pytest.mark.parametrize("submitted,expected", [("0", 0), ("1", 1), ("2", 2)])
def test_coerces_string_qos_to_int(submitted: str, expected: int) -> None:
    """The deploy form's select submits enum values as strings; coerce them to the int Literal."""
    s = Settings(qos=submitted)
    assert s.qos == expected and isinstance(s.qos, int)


def test_rejects_bool_qos() -> None:
    """bool is an int subclass, so qos=True would slip through as 1; it must be rejected."""
    with pytest.raises(ValidationError):
        Settings(qos=True)


@pytest.mark.parametrize("bad", [-1, 3, "3", "-1"])
def test_rejects_out_of_range_qos(bad: object) -> None:
    with pytest.raises(ValidationError):
        Settings(qos=bad)


def test_ignores_unknown_top_level_keys() -> None:
    assert Settings(some_platform_key="x").mqtt.port == 1883
