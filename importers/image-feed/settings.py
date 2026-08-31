from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Camera(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    images_dir: str = Field(default="images", min_length=1)     # default folder of images to replay
    publish_interval: int = Field(default=30, ge=1, le=3600)    # seconds between publishes


class Settings(BaseSettings):
    # extra="ignore": app_configuration may carry platform-injected keys; ignore them
    # rather than crash a valid deployment.
    model_config = SettingsConfigDict(extra="ignore")

    camera: Camera = Field(default_factory=Camera)
