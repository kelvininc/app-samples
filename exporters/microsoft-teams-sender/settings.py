from typing import Optional

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TeamsWebhook(BaseModel):
    """A Teams channel name and the incoming-webhook URL that posts to it."""

    channel: str
    url: SecretStr

    @field_validator("url", mode="before")
    @classmethod
    def _unresolved_secret_is_unset(cls, value: object) -> object:
        # An unresolved `<% secrets... %>` literal means the secret was never created
        # on the deployment; fail validation instead of posting the placeholder.
        if isinstance(value, str) and value.lstrip().startswith("<%"):
            return None
        return value


class Settings(BaseSettings):
    """App-level configuration read from `app.app_configuration`."""

    model_config = SettingsConfigDict(extra="ignore")

    webhooks: list[TeamsWebhook]

    def webhook_for(self, channel: str) -> Optional[TeamsWebhook]:
        return next((webhook for webhook in self.webhooks if webhook.channel == channel), None)
