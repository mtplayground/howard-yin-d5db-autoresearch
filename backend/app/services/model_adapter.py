from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from app.services.model_settings import EffectiveModelSettings

MessageRole = Literal["system", "user", "assistant"]


class ModelAdapterError(RuntimeError):
    pass


class ModelConfigurationError(ModelAdapterError):
    pass


class ModelProviderError(ModelAdapterError):
    pass


@dataclass(frozen=True)
class ModelMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ModelRequest:
    messages: list[ModelMessage]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    content: str
    model: str
    provider: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class ModelAdapter(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        ...


class OpenAICompatibleModelAdapter:
    def __init__(
        self,
        settings: EffectiveModelSettings,
        *,
        timeout_seconds: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not request.messages:
            raise ModelConfigurationError("at least one model message is required")
        if not self._settings.api_key:
            raise ModelConfigurationError("MODEL_API_KEY is required")

        model = request.model or self._settings.default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.__dict__ for message in request.messages],
            **request.extra,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        response_json = await self._post_chat_completions(payload)
        choices = response_json.get("choices") or []
        try:
            content = choices[0]["message"]["content"]
        except (IndexError, KeyError, TypeError) as exc:
            raise ModelProviderError("model provider response did not include assistant content") from exc
        if not isinstance(content, str):
            raise ModelProviderError("model provider assistant content was not text")

        return ModelResponse(
            content=content,
            model=str(response_json.get("model") or model),
            provider=self._settings.provider,
            usage=response_json.get("usage") or {},
            raw=response_json,
        )

    async def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = self._settings.base_url or "https://api.openai.com/v1"
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }

        try:
            if self._http_client:
                response = await self._http_client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ModelProviderError(f"model provider request failed with status {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError("model provider request failed") from exc
        except ValueError as exc:
            raise ModelProviderError("model provider response was not valid JSON") from exc

        if not isinstance(data, dict):
            raise ModelProviderError("model provider response was not a JSON object")
        return data


def build_model_adapter(settings: EffectiveModelSettings, *, timeout_seconds: float = 60.0) -> ModelAdapter:
    provider = settings.provider.lower()
    if provider not in {"openai", "openai-compatible"}:
        raise ModelConfigurationError(f"unsupported model provider: {settings.provider}")
    return OpenAICompatibleModelAdapter(settings, timeout_seconds=timeout_seconds)
