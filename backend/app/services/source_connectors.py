from __future__ import annotations

import asyncio
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import httpx

from app.core.config import Settings, get_settings
from app.services.retry import RetryPolicy, retry_async

SourceName = Literal["arxiv", "semantic_scholar", "github", "papers_with_code"]


class SourceConnectorError(RuntimeError):
    pass


class SourceConfigurationError(SourceConnectorError):
    pass


class SourceProviderError(SourceConnectorError):
    pass


@dataclass(frozen=True)
class SourceQuery:
    query: str
    limit: int = 10
    offset: int = 0

    def normalized(self) -> SourceQuery:
        text = self.query.strip()
        if not text:
            raise SourceConfigurationError("source query must not be empty")
        return SourceQuery(query=text, limit=max(1, min(self.limit, 100)), offset=max(0, self.offset))


@dataclass(frozen=True)
class SourceResult:
    source: SourceName
    external_id: str
    title: str
    url: str
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceSearchError:
    source: SourceName
    message: str


@dataclass(frozen=True)
class SourceSearchBatch:
    results: list[SourceResult]
    errors: list[SourceSearchError]


class SourceConnector(Protocol):
    source: SourceName

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        ...


class AsyncRateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval_seconds = max(0.0, min_interval_seconds)
        self._lock = asyncio.Lock()
        self._last_call_by_key: dict[str, float] = {}

    async def wait(self, key: str) -> None:
        if self._min_interval_seconds <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            earliest = self._last_call_by_key.get(key, 0.0) + self._min_interval_seconds
            delay = earliest - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._last_call_by_key[key] = now


class HttpSourceConnector:
    source: SourceName

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        user_agent: str,
        rate_limiter: AsyncRateLimiter,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._rate_limiter = rate_limiter
        self._http_client = http_client
        self._retry_policy = retry_policy or RetryPolicy()

    async def _get(self, path: str, *, params: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
        request_headers = {"User-Agent": self._user_agent, **(headers or {})}
        url = f"{self._base_url}/{path.lstrip('/')}" if path else self._base_url

        async def get_once() -> httpx.Response:
            await self._rate_limiter.wait(self.source)
            if self._http_client:
                response = await self._http_client.get(url, params=params, headers=request_headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.get(url, params=params, headers=request_headers)
            response.raise_for_status()
            return response

        try:
            return await retry_async(get_once, policy=self._retry_policy, is_retryable=_is_retryable_source_exception)
        except httpx.HTTPStatusError as exc:
            raise SourceProviderError(f"{self.source} request failed with status {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise SourceProviderError(f"{self.source} request failed") from exc

    @staticmethod
    def _json_object(response: httpx.Response, source: SourceName) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceProviderError(f"{source} response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise SourceProviderError(f"{source} response was not a JSON object")
        return payload


class ArxivConnector(HttpSourceConnector):
    source: SourceName = "arxiv"

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        normalized = query.normalized()
        response = await self._get(
            "",
            params={
                "search_query": f"all:{normalized.query}",
                "start": normalized.offset,
                "max_results": normalized.limit,
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
        )
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise SourceProviderError("arxiv response was not valid Atom XML") from exc

        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        results: list[SourceResult] = []
        for entry in root.findall("atom:entry", namespace):
            external_id = _text(entry, "atom:id", namespace) or str(uuid.uuid5(uuid.NAMESPACE_URL, ET.tostring(entry).decode()))
            title = _collapse_ws(_text(entry, "atom:title", namespace) or "Untitled arXiv result")
            abstract = _collapse_ws(_text(entry, "atom:summary", namespace) or "") or None
            authors = [
                name
                for author in entry.findall("atom:author", namespace)
                if (name := _collapse_ws(_text(author, "atom:name", namespace) or ""))
            ]
            results.append(
                SourceResult(
                    source=self.source,
                    external_id=external_id.rsplit("/", 1)[-1],
                    title=title,
                    url=external_id,
                    abstract=abstract,
                    authors=authors,
                    published_at=_parse_datetime(_text(entry, "atom:published", namespace)),
                    updated_at=_parse_datetime(_text(entry, "atom:updated", namespace)),
                    metadata={"raw_id": external_id},
                )
            )
        return results


class SemanticScholarConnector(HttpSourceConnector):
    source: SourceName = "semantic_scholar"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        normalized = query.normalized()
        headers = {"x-api-key": self._api_key} if self._api_key else None
        response = await self._get(
            "paper/search",
            params={
                "query": normalized.query,
                "limit": normalized.limit,
                "offset": normalized.offset,
                "fields": "paperId,title,abstract,url,year,authors,externalIds,citationCount,publicationDate,openAccessPdf",
            },
            headers=headers,
        )
        payload = self._json_object(response, self.source)
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise SourceProviderError("semantic_scholar response data was not a list")
        return [self._result_from_item(item) for item in data if isinstance(item, dict)]

    def _result_from_item(self, item: dict[str, Any]) -> SourceResult:
        external_id = str(item.get("paperId") or item.get("url") or uuid.uuid4())
        authors = [
            str(author.get("name"))
            for author in item.get("authors") or []
            if isinstance(author, dict) and author.get("name")
        ]
        return SourceResult(
            source=self.source,
            external_id=external_id,
            title=str(item.get("title") or "Untitled Semantic Scholar result"),
            url=str(item.get("url") or f"https://www.semanticscholar.org/paper/{external_id}"),
            abstract=item.get("abstract") if isinstance(item.get("abstract"), str) else None,
            authors=authors,
            published_at=_parse_date(item.get("publicationDate")),
            metadata={
                "year": item.get("year"),
                "external_ids": item.get("externalIds") or {},
                "citation_count": item.get("citationCount"),
                "open_access_pdf": item.get("openAccessPdf"),
            },
        )


class GitHubConnector(HttpSourceConnector):
    source: SourceName = "github"

    def __init__(self, *, token: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._token = token

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        normalized = query.normalized()
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        response = await self._get(
            "search/repositories",
            params={
                "q": normalized.query,
                "per_page": min(normalized.limit, 100),
                "page": normalized.offset // max(1, normalized.limit) + 1,
                "sort": "stars",
                "order": "desc",
            },
            headers=headers,
        )
        payload = self._json_object(response, self.source)
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise SourceProviderError("github response items was not a list")
        return [self._result_from_item(item) for item in items if isinstance(item, dict)]

    def _result_from_item(self, item: dict[str, Any]) -> SourceResult:
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        return SourceResult(
            source=self.source,
            external_id=str(item.get("id") or item.get("full_name") or uuid.uuid4()),
            title=str(item.get("full_name") or item.get("name") or "Untitled GitHub repository"),
            url=str(item.get("html_url") or ""),
            abstract=item.get("description") if isinstance(item.get("description"), str) else None,
            authors=[str(owner.get("login"))] if owner.get("login") else [],
            published_at=_parse_datetime(item.get("created_at")),
            updated_at=_parse_datetime(item.get("updated_at")),
            metadata={
                "stars": item.get("stargazers_count"),
                "language": item.get("language"),
                "topics": item.get("topics") or [],
            },
        )


class PapersWithCodeConnector(HttpSourceConnector):
    source: SourceName = "papers_with_code"

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        normalized = query.normalized()
        response = await self._get(
            "papers/",
            params={
                "q": normalized.query,
                "items_per_page": normalized.limit,
                "page": normalized.offset // max(1, normalized.limit) + 1,
            },
        )
        payload = self._json_object(response, self.source)
        items = payload.get("results") or []
        if not isinstance(items, list):
            raise SourceProviderError("papers_with_code response results was not a list")
        return [self._result_from_item(item) for item in items if isinstance(item, dict)]

    def _result_from_item(self, item: dict[str, Any]) -> SourceResult:
        url = item.get("url") or item.get("paper_url") or ""
        return SourceResult(
            source=self.source,
            external_id=str(item.get("id") or url or uuid.uuid4()),
            title=str(item.get("title") or "Untitled Papers with Code result"),
            url=str(url),
            abstract=item.get("abstract") if isinstance(item.get("abstract"), str) else None,
            authors=_authors_from_papers_with_code(item.get("authors")),
            published_at=_parse_date(item.get("published")),
            metadata={
                "paper_url": item.get("paper_url"),
                "repository_url": item.get("repository_url"),
                "proceeding": item.get("proceeding"),
            },
        )


class SourceSearchClient:
    def __init__(self, connectors: Sequence[SourceConnector]) -> None:
        self._connectors = tuple(connectors)

    @property
    def sources(self) -> tuple[SourceName, ...]:
        return tuple(connector.source for connector in self._connectors)

    async def search_all(self, query: SourceQuery, *, sources: Iterable[SourceName] | None = None) -> SourceSearchBatch:
        selected = set(sources) if sources else set(self.sources)
        results: list[SourceResult] = []
        errors: list[SourceSearchError] = []
        for connector in self._connectors:
            if connector.source not in selected:
                continue
            try:
                results.extend(await connector.search(query))
            except SourceConnectorError as exc:
                errors.append(SourceSearchError(source=connector.source, message=str(exc)))
        return SourceSearchBatch(results=results, errors=errors)


def build_source_connectors(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    rate_limiter: AsyncRateLimiter | None = None,
    retry_policy: RetryPolicy | None = None,
) -> tuple[SourceConnector, ...]:
    resolved_settings = settings or get_settings()
    enabled = _enabled_sources(resolved_settings.source_connectors_enabled)
    limiter = rate_limiter or AsyncRateLimiter(resolved_settings.source_min_interval_seconds)
    common: dict[str, Any] = {
        "timeout_seconds": resolved_settings.source_request_timeout_seconds,
        "user_agent": resolved_settings.source_user_agent,
        "rate_limiter": limiter,
        "http_client": http_client,
        "retry_policy": retry_policy,
    }
    connectors: list[SourceConnector] = []
    if "arxiv" in enabled:
        connectors.append(ArxivConnector(base_url=resolved_settings.arxiv_api_url, **common))
    if "semantic_scholar" in enabled:
        api_key = (
            resolved_settings.semantic_scholar_api_key.get_secret_value()
            if resolved_settings.semantic_scholar_api_key
            else None
        )
        connectors.append(SemanticScholarConnector(base_url=resolved_settings.semantic_scholar_api_url, api_key=api_key, **common))
    if "github" in enabled:
        token = resolved_settings.github_token.get_secret_value() if resolved_settings.github_token else None
        connectors.append(GitHubConnector(base_url=resolved_settings.github_api_url, token=token, **common))
    if "papers_with_code" in enabled:
        connectors.append(PapersWithCodeConnector(base_url=resolved_settings.papers_with_code_api_url, **common))
    return tuple(connectors)


def build_source_search_client(settings: Settings | None = None) -> SourceSearchClient:
    return SourceSearchClient(build_source_connectors(settings))


def _enabled_sources(value: str) -> set[SourceName]:
    enabled: set[SourceName] = set()
    valid_sources = {"arxiv", "semantic_scholar", "github", "papers_with_code"}
    for raw_source in value.split(","):
        source = raw_source.strip().lower().replace("-", "_")
        if not source:
            continue
        if source not in valid_sources:
            raise SourceConfigurationError(f"unsupported source connector: {raw_source.strip()}")
        enabled.add(source)  # type: ignore[arg-type]
    return enabled


def _text(element: ET.Element, path: str, namespace: dict[str, str]) -> str | None:
    child = element.find(path, namespace)
    return child.text if child is not None else None


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromisoformat(f"{value}T00:00:00")
        except ValueError:
            return None


def _authors_from_papers_with_code(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for author in value:
        if isinstance(author, str):
            authors.append(author)
        elif isinstance(author, dict) and author.get("name"):
            authors.append(str(author["name"]))
    return authors


def _is_retryable_source_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code in {408, 409, 425, 429} or status_code >= 500
    return isinstance(exc, httpx.HTTPError)
