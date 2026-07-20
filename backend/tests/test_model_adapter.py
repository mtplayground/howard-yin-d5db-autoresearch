import asyncio
import json
import unittest

import httpx

from app.services.model_adapter import (
    ModelConfigurationError,
    ModelMessage,
    ModelRequest,
    OpenAICompatibleModelAdapter,
    build_model_adapter,
)
from app.services.model_settings import EffectiveModelSettings


class ModelAdapterTest(unittest.TestCase):
    def test_openai_compatible_adapter_posts_chat_completion(self) -> None:
        async def run() -> None:
            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.url, "https://models.example/v1/chat/completions")
                self.assertEqual(request.headers["authorization"], "Bearer test-key")
                payload = json.loads(request.content)
                self.assertEqual(payload["model"], "model-a")
                self.assertEqual(payload["messages"], [{"role": "user", "content": "hello"}])
                return httpx.Response(
                    200,
                    json={
                        "model": "model-a",
                        "choices": [{"message": {"content": "world"}}],
                        "usage": {"total_tokens": 2},
                    },
                )

            settings = EffectiveModelSettings(
                provider="openai-compatible",
                base_url="https://models.example/v1",
                default_model="model-a",
                api_key="test-key",
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = OpenAICompatibleModelAdapter(settings, http_client=client)
                response = await adapter.complete(ModelRequest(messages=[ModelMessage(role="user", content="hello")]))

            self.assertEqual(response.content, "world")
            self.assertEqual(response.model, "model-a")
            self.assertEqual(response.usage["total_tokens"], 2)

        asyncio.run(run())

    def test_requires_api_key(self) -> None:
        settings = EffectiveModelSettings(
            provider="openai",
            base_url=None,
            default_model="model-a",
            api_key=None,
        )
        adapter = OpenAICompatibleModelAdapter(settings)

        with self.assertRaises(ModelConfigurationError):
            asyncio.run(adapter.complete(ModelRequest(messages=[ModelMessage(role="user", content="hello")])))

    def test_rejects_unsupported_provider(self) -> None:
        settings = EffectiveModelSettings(
            provider="other",
            base_url=None,
            default_model="model-a",
            api_key="key",
        )

        with self.assertRaises(ModelConfigurationError):
            build_model_adapter(settings)


if __name__ == "__main__":
    unittest.main()
