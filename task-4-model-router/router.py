"""One primary, one backup, with a fresh token reservation for each attempt."""

import asyncio

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


PROVIDER_TIMEOUT_SECONDS = 3.0


class GatewayError(Exception):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Usage(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    input_tokens: int = Field(ge=0, le=1_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000)


class Completion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str = Field(max_length=65_536)
    usage: Usage


class ModelRouter:
    def __init__(self, client, limiter, primary_url, backup_url):
        self.client, self.limiter = client, limiter
        self.providers = [("primary", primary_url), ("backup", backup_url)]

    async def complete(self, tenant_hash: str, prompt: str, max_output_tokens: int):
        # The demo contract bounds input tokens by UTF-8 bytes, not word count.
        input_budget = len(prompt.encode("utf-8"))
        budget = input_budget + max_output_tokens
        payload = {"prompt": prompt, "max_output_tokens": max_output_tokens}

        for name, url in self.providers:
            reservation = await self.limiter.reserve(tenant_hash, budget)
            try:
                # Cancel the local attempt at 3000 ms, including a trickling body.
                async with asyncio.timeout(PROVIDER_TIMEOUT_SECONDS):
                    response = await self.client.post(url, json=payload,
                                                      headers={"Cookie": "", "Accept-Encoding": "identity"})
            except (TimeoutError, httpx.TimeoutException):
                # The remote provider may still have consumed tokens. Keep the charge.
                if name == "primary":
                    continue
                raise GatewayError("providers_unavailable", "Unable to complete the request", 504) from None
            except httpx.RequestError:
                raise GatewayError("providers_unavailable", "Unable to complete the request") from None

            if response.status_code == 429:
                # In this provider contract, 429 means no generation was accepted.
                await self.limiter.settle(reservation, 0)
                if name == "primary":
                    continue
                raise GatewayError("providers_unavailable", "Unable to complete the request")
            if response.status_code != 200:
                raise GatewayError("providers_unavailable", "Unable to complete the request")

            try:
                result = Completion.model_validate_json(response.content)
            except ValidationError:
                # Unknown usage keeps the full reservation; never echo provider data.
                raise GatewayError("invalid_provider_response", "Provider returned an invalid response") from None
            actual = result.usage.input_tokens + result.usage.output_tokens
            await self.limiter.settle(reservation, actual)
            if result.usage.input_tokens > input_budget or result.usage.output_tokens > max_output_tokens:
                # Keep the overrun visible to future budget checks rather than hide it.
                raise GatewayError("invalid_provider_response", "Provider exceeded its token contract")
            return {"text": result.text, "provider": name, "usage": result.usage.model_dump()}

        raise GatewayError("providers_unavailable", "Unable to complete the request")
