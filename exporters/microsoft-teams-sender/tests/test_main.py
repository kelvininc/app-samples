"""Tests for the Microsoft Teams Sender exporter."""

from __future__ import annotations

from datetime import timedelta

import pytest
from main import ExporterApplication
from pydantic import ValidationError
from settings import Settings
from teams_integration import TeamsIntegration

from kelvin.krn import KRNAsset
from kelvin.message import CustomAction, CustomActionResultMsg
from kelvin.testing import KelvinAppTest, ManifestBuilder

exporter = ExporterApplication()

CONFIGURATION = {"webhooks": [{"channel": "general", "url": "https://example.com/webhook/general"}]}


def _build_manifest(configuration: dict | None = None) -> ManifestBuilder:
    # Mirror custom_actions.inputs from app.yaml.
    return (
        ManifestBuilder()
        .add_custom_action_input("Teams Message")
        .add_asset("test-asset-1")
        .set_configuration(configuration if configuration is not None else CONFIGURATION)
    )


def _action(payload: dict, type: str = "Teams Message") -> CustomAction:
    return CustomAction(
        resource=KRNAsset("test-asset-1"),
        type=type,
        title="Teams Test Message",
        expiration_date=timedelta(minutes=5),
        payload=payload,
    )


def _results(harness: KelvinAppTest) -> list[CustomActionResultMsg]:
    return [o for o in harness.outputs if isinstance(o, CustomActionResultMsg)]


class TestSettings:
    def test_ignores_platform_injected_keys(self) -> None:
        settings = Settings(**CONFIGURATION, injected_by_platform="ignored")

        assert settings.webhook_for("general") is not None

    def test_unresolved_secret_fails_validation(self) -> None:
        with pytest.raises(ValidationError):
            Settings(webhooks=[{"channel": "general", "url": "<% secrets.teams-webhook-general %>"}])

    def test_missing_webhooks_fails_validation(self) -> None:
        with pytest.raises(ValidationError):
            Settings()

    def test_webhook_for_unknown_channel_returns_none(self) -> None:
        settings = Settings(**CONFIGURATION)

        assert settings.webhook_for("unknown-channel") is None


class TestTeamsIntegration:
    @pytest.mark.asyncio
    async def test_posts_adaptive_card_to_channel_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posts: list[tuple[str, dict]] = []

        async def fake_post(self: TeamsIntegration, url: str, payload: dict) -> tuple[int, str]:
            posts.append((url, payload))
            return 202, ""

        monkeypatch.setattr(TeamsIntegration, "_post", fake_post)
        integration = TeamsIntegration(settings=Settings(**CONFIGURATION))

        response = await integration.send_teams_message(channel_name="general", message="Hello Teams")

        assert response.success is True
        url, payload = posts[0]
        assert url == "https://example.com/webhook/general"
        assert payload["attachments"][0]["content"]["body"][0]["text"] == "Hello Teams"

    @pytest.mark.asyncio
    async def test_unknown_channel_fails_without_posting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_post(self: TeamsIntegration, url: str, payload: dict) -> tuple[int, str]:
            raise AssertionError("must not post when the channel has no webhook")

        monkeypatch.setattr(TeamsIntegration, "_post", fake_post)
        integration = TeamsIntegration(settings=Settings(**CONFIGURATION))

        response = await integration.send_teams_message(channel_name="unknown-channel", message="Hello")

        assert response.success is False
        assert "unknown-channel" in response.message

    @pytest.mark.asyncio
    async def test_http_error_reports_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_post(self: TeamsIntegration, url: str, payload: dict) -> tuple[int, str]:
            return 400, "Invalid payload"

        monkeypatch.setattr(TeamsIntegration, "_post", fake_post)
        integration = TeamsIntegration(settings=Settings(**CONFIGURATION))

        response = await integration.send_teams_message(channel_name="general", message="Hello")

        assert response.success is False
        assert "400" in response.message

    @pytest.mark.asyncio
    async def test_network_error_reports_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import aiohttp

        async def fake_post(self: TeamsIntegration, url: str, payload: dict) -> tuple[int, str]:
            raise aiohttp.ClientConnectionError("connection refused")

        monkeypatch.setattr(TeamsIntegration, "_post", fake_post)
        integration = TeamsIntegration(settings=Settings(**CONFIGURATION))

        response = await integration.send_teams_message(channel_name="general", message="Hello")

        assert response.success is False


class TestOnCustomAction:
    @pytest.mark.asyncio
    async def test_acks_success_when_message_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_post(self: TeamsIntegration, url: str, payload: dict) -> tuple[int, str]:
            return 202, ""

        monkeypatch.setattr(TeamsIntegration, "_post", fake_post)
        harness = KelvinAppTest(exporter.app, manifest=_build_manifest().build())
        async with harness:
            await harness.publish(_action({"channel": "general", "message": {"text": "Hello Teams"}}))
            await harness.run_until_idle()

        results = _results(harness)
        assert len(results) == 1
        assert results[0].payload.success is True

    @pytest.mark.asyncio
    async def test_missing_channel_acks_failure(self) -> None:
        harness = KelvinAppTest(exporter.app, manifest=_build_manifest().build())
        async with harness:
            await harness.publish(_action({"message": {"text": "Hello Teams"}}))
            await harness.run_until_idle()

        results = _results(harness)
        assert len(results) == 1
        assert results[0].payload.success is False

    @pytest.mark.asyncio
    async def test_missing_message_acks_failure(self) -> None:
        harness = KelvinAppTest(exporter.app, manifest=_build_manifest().build())
        async with harness:
            await harness.publish(_action({"channel": "general"}))
            await harness.run_until_idle()

        results = _results(harness)
        assert len(results) == 1
        assert results[0].payload.success is False

    @pytest.mark.asyncio
    async def test_unconfigured_webhooks_acks_failure(self) -> None:
        harness = KelvinAppTest(exporter.app, manifest=_build_manifest(configuration={}).build())
        async with harness:
            await harness.publish(_action({"channel": "general", "message": {"text": "Hello Teams"}}))
            await harness.run_until_idle()

        results = _results(harness)
        assert len(results) == 1
        assert results[0].payload.success is False

    @pytest.mark.asyncio
    async def test_unknown_action_type_is_ignored(self) -> None:
        manifest = _build_manifest().add_custom_action_input("Email Message").build()
        harness = KelvinAppTest(exporter.app, manifest=manifest)
        async with harness:
            await harness.publish(_action({"channel": "general", "message": {"text": "Hello"}}, type="Email Message"))
            await harness.run_until_idle()

        assert _results(harness) == []
