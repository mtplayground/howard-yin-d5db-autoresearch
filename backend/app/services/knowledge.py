from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import KnowledgeItem
from app.services.source_connectors import SourceName, SourceResult


class KnowledgeNormalizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedKnowledgeItem:
    canonical_key: str
    source: SourceName
    source_id: str
    title: str
    abstract: str | None
    url: str
    code_repository_url: str | None
    authors: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeIngestResult:
    items: list[KnowledgeItem]
    created_count: int
    updated_count: int


def normalize_source_result(result: SourceResult) -> NormalizedKnowledgeItem:
    title = _collapse_ws(result.title)
    if not title:
        raise KnowledgeNormalizationError("source result title must not be empty")
    source_id = _collapse_ws(result.external_id)
    if not source_id:
        raise KnowledgeNormalizationError("source result external_id must not be empty")
    url = _normalize_url(result.url)
    if not url:
        raise KnowledgeNormalizationError("source result url must not be empty")

    metadata = dict(result.metadata or {})
    metadata["source_observations"] = {
        result.source: {
            "external_id": source_id,
            "url": url,
            "metadata": result.metadata or {},
        }
    }
    return NormalizedKnowledgeItem(
        canonical_key=_canonical_key(result, title, url),
        source=result.source,
        source_id=source_id,
        title=title,
        abstract=_optional_text(result.abstract),
        url=url,
        code_repository_url=_extract_code_repository_url(result),
        authors=_dedupe_strings(result.authors),
        published_at=result.published_at,
        source_updated_at=result.updated_at,
        source_metadata=metadata,
    )


def ingest_source_results(db: Session, results: list[SourceResult]) -> KnowledgeIngestResult:
    normalized_items = [normalize_source_result(result) for result in results]
    persisted: list[KnowledgeItem] = []
    created_count = 0
    updated_count = 0
    for item in normalized_items:
        existing = (
            db.query(KnowledgeItem)
            .filter((KnowledgeItem.canonical_key == item.canonical_key) | ((KnowledgeItem.source == item.source) & (KnowledgeItem.source_id == item.source_id)))
            .one_or_none()
        )
        if existing:
            _merge_knowledge_item(existing, item)
            updated_count += 1
            persisted.append(existing)
            continue

        record = KnowledgeItem(
            canonical_key=item.canonical_key,
            source=item.source,
            source_id=item.source_id,
            title=item.title,
            abstract=item.abstract,
            url=item.url,
            code_repository_url=item.code_repository_url,
            authors=item.authors,
            published_at=item.published_at,
            source_updated_at=item.source_updated_at,
            source_metadata=item.source_metadata,
        )
        db.add(record)
        db.flush()
        persisted.append(record)
        created_count += 1

    db.commit()
    for record in persisted:
        db.refresh(record)
    return KnowledgeIngestResult(items=persisted, created_count=created_count, updated_count=updated_count)


def _merge_knowledge_item(record: KnowledgeItem, item: NormalizedKnowledgeItem) -> None:
    record.title = item.title or record.title
    record.abstract = item.abstract or record.abstract
    record.url = item.url or record.url
    record.code_repository_url = item.code_repository_url or record.code_repository_url
    record.authors = _dedupe_strings([*(record.authors or []), *item.authors])
    record.published_at = record.published_at or item.published_at
    record.source_updated_at = _latest_datetime(record.source_updated_at, item.source_updated_at)
    record.source_metadata = _merge_metadata(record.source_metadata or {}, item.source_metadata)
    record.last_seen_at = func.now()


def _canonical_key(result: SourceResult, title: str, url: str) -> str:
    for candidate in _canonical_candidates(result, title, url):
        if candidate:
            return hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    raise KnowledgeNormalizationError("source result did not produce a canonical key")


def _canonical_candidates(result: SourceResult, title: str, url: str) -> list[str]:
    metadata = result.metadata or {}
    external_ids = metadata.get("external_ids") if isinstance(metadata.get("external_ids"), dict) else {}
    doi = _normalize_doi(external_ids.get("DOI") or external_ids.get("doi") or metadata.get("doi"))
    arxiv_id = _normalize_arxiv_id(
        external_ids.get("ArXiv")
        or external_ids.get("ARXIV")
        or external_ids.get("arxiv")
        or metadata.get("arxiv_id")
        or _arxiv_id_from_value(result.external_id)
        or _arxiv_id_from_value(url)
    )
    github_repo = _github_repo_from_url(url) if result.source == "github" else None
    return [
        f"doi:{doi}" if doi else "",
        f"arxiv:{arxiv_id}" if arxiv_id else "",
        f"github:{github_repo}" if github_repo else "",
        f"url:{url}",
        f"title:{title.lower()}",
    ]


def _extract_code_repository_url(result: SourceResult) -> str | None:
    if result.source == "github":
        return _normalize_url(result.url)
    metadata = result.metadata or {}
    for key in ("code_repository_url", "repository_url", "repo_url", "github_url"):
        if value := _normalize_url(metadata.get(key)):
            return value
    return None


def _merge_metadata(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {**existing, **incoming}
    existing_observations = existing.get("source_observations") if isinstance(existing.get("source_observations"), dict) else {}
    incoming_observations = incoming.get("source_observations") if isinstance(incoming.get("source_observations"), dict) else {}
    if existing_observations or incoming_observations:
        merged["source_observations"] = {**existing_observations, **incoming_observations}
    return merged


def _normalize_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = _collapse_ws(value)
    return text or None


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        clean = _collapse_ws(value)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            deduped.append(clean)
    return deduped


def _latest_datetime(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _normalize_doi(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    clean = value.strip().lower()
    clean = re.sub(r"^https?://(dx\.)?doi\.org/", "", clean)
    clean = clean.removeprefix("doi:")
    return clean.rstrip("/")


def _normalize_arxiv_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    clean = value.strip()
    clean = clean.rsplit("/", 1)[-1]
    clean = clean.removeprefix("abs:")
    clean = re.sub(r"v\d+$", "", clean)
    return clean.lower()


def _arxiv_id_from_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if "arxiv.org/" not in value and not re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", value):
        return ""
    return _normalize_arxiv_id(value)


def _github_repo_from_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.netloc.lower() not in {"github.com", "www.github.com"}:
        return ""
    path_parts = [part for part in parts.path.strip("/").split("/") if part]
    if len(path_parts) < 2:
        return ""
    return f"{path_parts[0].lower()}/{path_parts[1].lower()}"
