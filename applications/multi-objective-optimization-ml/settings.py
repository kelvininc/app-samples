from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Window(BaseModel):
    # run_model fits its regression models on at least 100 rows, so the window floor is 100;
    # a smaller value is rejected at startup rather than silently never producing a recommendation.
    rows: int = Field(default=100, ge=100)              # rows of history the models fit on
    retrain_every_rows: int = Field(default=1, ge=1)    # re-run the optimizer every N new rows
    round_seconds: float = Field(default=1.0, gt=0)     # merge readings within this interval into one row


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    window: Window = Field(default_factory=Window)
