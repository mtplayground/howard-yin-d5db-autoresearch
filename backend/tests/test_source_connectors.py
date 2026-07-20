import asyncio
import json
import unittest

import httpx

from app.core.config import Settings
from app.services.source_connectors import (
    AsyncRateLimiter,
    SourceConfigurationError,
    SourceQuery,
    SourceSearchClient,
    build_source_connectors,
)


class SourceConnectorsTest(unittest.TestCase):
    def test_builds_enabled_connectors_from_settings(self) -> None:
        settings = Settings(
            database_url="postgresql://user:pass@example/db",
            source_connectors_enabled="arxiv,github",
            source_min_interval_seconds=0,
        )

        connectors = build_source_connectors(settings, rate_limiter=AsyncRateLimiter(0))

        self.assertEqual([connector.source for connector in connectors], ["arxiv", "github"])

    def test_rejects_unknown_source(self) -> None:
        settings = Settings(
            database_url="postgresql://user:pass@example/db",
            source_connectors_enabled="arxiv,unknown",
        )

        with self.assertRaises(SourceConfigurationError):
            build_source_connectors(settings)

    def test_search_all_normalizes_each_source(self) -> None:
        async def run() -> None:
            requests: list[httpx.Request] = []

            async def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                if request.url.host == "arxiv.example":
                    return httpx.Response(
                        200,
                        text="""<?xml version="1.0" encoding="UTF-8"?>
                        <feed xmlns="http://www.w3.org/2005/Atom">
                          <entry>
                            <id>https://arxiv.org/abs/2401.00001</id>
                            <title> Retrieval augmented testing </title>
                            <summary> A compact summary. </summary>
                            <published>2024-01-01T00:00:00Z</published>
                            <updated>2024-01-02T00:00:00Z</updated>
                            <author><name>Ada Lovelace</name></author>
                          </entry>
                        </feed>""",
                    )
                if request.url.host == "semanticscholar.example":
                    self.assertEqual(request.headers["x-api-key"], "semantic-key")
                    return httpx.Response(
                        200,
                        json={
                            "data": [
                                {
                                    "paperId": "paper-1",
                                    "title": "Semantic result",
                                    "abstract": "Abstract",
                                    "url": "https://semanticscholar.org/paper-1",
                                    "authors": [{"name": "Grace Hopper"}],
                                    "publicationDate": "2024-02-03",
                                    "citationCount": 7,
                                }
                            ]
                        },
                    )
                if request.url.host == "github.example":
                    self.assertEqual(request.headers["authorization"], "Bearer gh-token")
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "id": 123,
                                    "full_name": "org/repo",
                                    "description": "Repository",
                                    "html_url": "https://github.example/org/repo",
                                    "owner": {"login": "org"},
                                    "stargazers_count": 42,
                                    "created_at": "2024-01-01T00:00:00Z",
                                }
                            ]
                        },
                    )
                if request.url.host == "paperswithcode.example":
                    return httpx.Response(
                        200,
                        json={
                            "results": [
                                {
                                    "id": "pwc-1",
                                    "title": "PWC result",
                                    "abstract": "Paper abstract",
                                    "url": "https://paperswithcode.example/paper",
                                    "authors": [{"name": "Katherine Johnson"}],
                                    "published": "2024-03-04",
                                }
                            ]
                        },
                    )
                return httpx.Response(404)

            settings = Settings(
                database_url="postgresql://user:pass@example/db",
                arxiv_api_url="https://arxiv.example/api/query",
                semantic_scholar_api_url="https://semanticscholar.example/graph/v1",
                github_api_url="https://github.example",
                papers_with_code_api_url="https://paperswithcode.example/api/v1",
                source_min_interval_seconds=0,
                semantic_scholar_api_key="semantic-key",
                github_token="gh-token",
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                connectors = build_source_connectors(settings, http_client=client, rate_limiter=AsyncRateLimiter(0))
                batch = await SourceSearchClient(connectors).search_all(SourceQuery(query="retrieval", limit=2))

            self.assertEqual(batch.errors, [])
            self.assertEqual([result.source for result in batch.results], ["arxiv", "semantic_scholar", "github", "papers_with_code"])
            self.assertEqual(batch.results[0].external_id, "2401.00001")
            self.assertEqual(batch.results[1].authors, ["Grace Hopper"])
            self.assertEqual(batch.results[2].metadata["stars"], 42)
            self.assertEqual(batch.results[3].authors, ["Katherine Johnson"])
            self.assertEqual(len(requests), 4)

        asyncio.run(run())

    def test_source_errors_are_collected(self) -> None:
        async def run() -> None:
            async def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(429, json={"message": "rate limited"})

            settings = Settings(
                database_url="postgresql://user:pass@example/db",
                source_connectors_enabled="github",
                github_api_url="https://github.example",
                source_min_interval_seconds=0,
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                connectors = build_source_connectors(settings, http_client=client, rate_limiter=AsyncRateLimiter(0))
                batch = await SourceSearchClient(connectors).search_all(SourceQuery(query="retrieval"))

            self.assertEqual(batch.results, [])
            self.assertEqual(batch.errors[0].source, "github")
            self.assertIn("429", batch.errors[0].message)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
