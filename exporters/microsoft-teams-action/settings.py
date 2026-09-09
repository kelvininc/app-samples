import re

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_UNRESOLVED_SECRET_RE = re.compile(r"<%\s*secrets\.")

# HttpUrl allows only the http and https schemes, so it rejects ftp:// and bare strings.
_HTTP_URL = TypeAdapter(HttpUrl)


def normalize_channel(name: str) -> str:
    """Key a channel label for lookup: whitespace-stripped and case-folded.

    The label is typed by hand twice, once in the configuration and once in every action
    payload, and Teams channel names aren't case-normalized, so failing "Alerts" against a
    configured "alerts" would only cost the operator time. The label never reaches Teams.
    """
    return name.strip().casefold()


class TeamsWebhook(BaseModel):
    """A channel label and the Teams incoming webhook that posts to it.

    A Teams webhook URL is bound to the channel it was created in, so posting to N channels
    means N webhooks. `channel` is a local label that the action payload selects by; Teams
    never sees it, and nothing here can verify it names the channel the webhook posts to.
    """

    channel: str
    # The webhook URL embeds a token, so it's a SecretStr; a stray log/repr masks it.
    url: SecretStr

    @field_validator("channel")
    @classmethod
    def _reject_blank_channel(cls, v: str) -> str:
        # A blank label can't be selected by any payload, so the entry would be dead weight
        # that silently swallows one of the configured webhooks.
        v = v.strip()
        if not v:
            raise ValueError("channel must not be blank")
        return v

    @field_validator("url", mode="before")
    @classmethod
    def _reject_unresolved_secret(cls, v: object) -> object:
        # An unconfigured secret arrives as the literal "<% secrets.x %>"; treat it as unset
        # so a deployment that forgot to wire the secret fails fast (url is required).
        return None if isinstance(v, str) and _UNRESOLVED_SECRET_RE.search(v) else v

    @field_validator("url")
    @classmethod
    def _reject_blank(cls, v: SecretStr) -> SecretStr:
        # A blank (or whitespace-only) URL would only fail later, on the first send;
        # fail configuration validation instead so the app never starts half-configured.
        if not v.get_secret_value().strip():
            raise ValueError("url must not be blank")
        return v

    @field_validator("url")
    @classmethod
    def _require_http_url(cls, v: SecretStr) -> SecretStr:
        # A typo'd webhook would only surface as a failed POST on the first action. The raised
        # message omits the value, because the URL embeds a token. Note that pydantic still records
        # the raw input on the error itself, so callers must report these with include_input=False
        # (main.py does) rather than str(exc).
        try:
            _HTTP_URL.validate_python(v.get_secret_value().strip())
        except ValidationError:
            raise ValueError("url must be a valid http(s) URL") from None
        return v


class Teams(BaseModel):
    """Every channel this exporter can post to, one webhook each."""

    # min_length=1: an empty list passes "webhooks is required" but leaves every action with
    # nowhere to go, so the failure would only show up per-action at runtime.
    webhooks: list[TeamsWebhook] = Field(min_length=1)

    @field_validator("webhooks")
    @classmethod
    def _reject_duplicate_channels(cls, webhooks: list[TeamsWebhook]) -> list[TeamsWebhook]:
        # Two entries sharing a label would make the lookup pick one and ignore the other, so
        # messages would land in a channel nobody chose and nothing would report it.
        seen: set[str] = set()
        for webhook in webhooks:
            key = normalize_channel(webhook.channel)
            if key in seen:
                raise ValueError(f"duplicate channel '{webhook.channel}'")
            seen.add(key)
        return webhooks


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    teams: Teams
