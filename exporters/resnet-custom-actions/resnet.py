from typing import Any, Dict

import httpx
from kelvin.logs import logger
from pydantic import BaseModel, Field


class ResnetIntegrationResponse(BaseModel):
    success: bool
    message: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResnetIntegration:

    def __init__(self, url: str, tenant: str, api_key: str) -> None:

        if url is None:
            raise ValueError("URL is not set in the app configuration")

        if tenant is None:
            raise ValueError("Tenant is not set in the app configuration")

        if api_key is None:
            raise ValueError("API key is not set in the app configuration")

        self.url = url
        self.tenant = tenant
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": f"{self.tenant}:{self.api_key}",
        }

    async def create_issue(self, payload: dict) -> ResnetIntegrationResponse:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
            try:
                logger.info("Creating issue in Resnet", url=self.url, tenant=self.tenant, payload=payload)

                # Send the POST request to the Resnet API
                response = await client.post(self.url, headers=self.headers, json={"data": payload})
                response.raise_for_status()  # Raise an error for bad responses

                # Parse response
                status_code = response.status_code
                metadata = response.json() if response.content else {}

                logger.info("Successfully created issue in Resnet", status_code=status_code, metadata=metadata)

                return ResnetIntegrationResponse(success=True, message="Issue created successfully", metadata=metadata)
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                metadata = e.response.json() if e.response.content else {}
                logger.error(f"Failed to create issue in Resnet: API error ({status_code})", status_code=status_code, metadata=metadata)
                return ResnetIntegrationResponse(success=False, message=f"Failed to create issue in Resnet: API error ({status_code})", metadata=metadata)
            except httpx.RequestError as e:
                logger.error("Failed to create issue in Resnet: Network error", error=e)
                return ResnetIntegrationResponse(success=False, message=f"Failed to create issue in Resnet: Network error: {e}", metadata={})
