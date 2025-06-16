from typing import Any, Dict

import httpx
from kelvin.logs import logger
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ResnetConfiguration(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    """Configuration for Resnet integration."""
    url: str = Field(..., description="The URL of the Resnet API endpoint")
    tenant: str = Field(..., description="The tenant identifier for the Resnet API")
    api_key: str = Field(..., description="The API key for authenticating with the Resnet API")


class ResnetIntegrationResponse(BaseModel):
    success: bool
    message: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResnetIntegration:
    def __init__(self, config: ResnetConfiguration) -> None:
        self.config = config
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": f"{self.config.tenant}:{self.config.api_key}",
        }

    async def create_issue(self, payload: dict) -> ResnetIntegrationResponse:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
            try:
                logger.info("Creating issue in Resnet", url=self.config.url, tenant=self.config.tenant, payload=payload)

                # Send the POST request to the Resnet API
                response = await client.post(self.config.url, headers=self.headers, json={"data": payload})
                response.raise_for_status()  # Raise an error for bad responses

                # Parse response
                status_code = response.status_code
                response_body = response.json() if response.content else {}
                metadata = {"response_status_code": status_code, "response_body": response_body}

                logger.info("Successfully created issue in Resnet", status_code=status_code, response_body=response_body)

                return ResnetIntegrationResponse(success=True, message="Issue created successfully", metadata=metadata)
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                response_body = e.response.json() if e.response.content else {}
                metadata = {"response_status_code": status_code, "response_body": response_body}
                logger.error(
                    f"Failed to create issue in Resnet: API error ({status_code})", status_code=status_code, response_body=response_body
                )
                return ResnetIntegrationResponse(
                    success=False, message=f"Failed to create issue in Resnet: API error ({status_code})", metadata=metadata
                )
            except httpx.RequestError as e:
                logger.error("Failed to create issue in Resnet: Network error", error=e)
                return ResnetIntegrationResponse(
                    success=False, message=f"Failed to create issue in Resnet: Network error: {e}", metadata={"error": str(e)}
                )
