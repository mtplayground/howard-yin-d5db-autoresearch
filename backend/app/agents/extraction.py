from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import KnowledgeItem
from app.services.model_adapter import ModelAdapter, ModelAdapterError, ModelMessage, ModelRequest, build_model_adapter
from app.services.model_settings import load_effective_model_settings


class ExtractionAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractionResult:
    summary: str
    methods: list[str] = field(default_factory=list)
    contributions: list[str] = field(default_factory=list)
    reusable_points: list[str] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class KnowledgeExtractionAgent:
    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    async def extract(self, item: KnowledgeItem) -> ExtractionResult:
        try:
            response = await self._adapter.complete(
                ModelRequest(
                    messages=[
                        ModelMessage(
                            role="system",
                            content=(
                                "You extract reusable research knowledge. Return strict JSON only with keys "
                                "summary, methods, contributions, reusable_points. Each list must contain concise strings."
                            ),
                        ),
                        ModelMessage(role="user", content=_knowledge_prompt(item)),
                    ],
                    temperature=0.1,
                    max_tokens=900,
                )
            )
        except ModelAdapterError as exc:
            raise ExtractionAgentError(str(exc)) from exc

        payload = _parse_json_object(response.content)
        return ExtractionResult(
            summary=_required_text(payload.get("summary"), "summary"),
            methods=_text_list(payload.get("methods")),
            contributions=_text_list(payload.get("contributions")),
            reusable_points=_text_list(payload.get("reusable_points")),
            model=response.model,
            provider=response.provider,
            usage=response.usage,
        )


async def extract_and_update_knowledge_item(
    db: Session,
    knowledge_item_id: uuid.UUID,
    agent: KnowledgeExtractionAgent,
) -> KnowledgeItem:
    item = db.get(KnowledgeItem, knowledge_item_id)
    if item is None:
        raise ExtractionAgentError(f"Knowledge item {knowledge_item_id} was not found")
    result = await agent.extract(item)
    _apply_extraction(item, result)
    db.commit()
    db.refresh(item)
    return item


async def extract_with_configured_model(
    db: Session,
    settings: Settings,
    knowledge_item_id: uuid.UUID,
) -> KnowledgeItem:
    effective_settings = load_effective_model_settings(db, settings)
    adapter = build_model_adapter(effective_settings, timeout_seconds=settings.model_request_timeout_seconds)
    return await extract_and_update_knowledge_item(db, knowledge_item_id, KnowledgeExtractionAgent(adapter))


def _apply_extraction(item: KnowledgeItem, result: ExtractionResult) -> None:
    item.summary = result.summary
    item.methods = result.methods
    item.contributions = result.contributions
    item.reusable_points = result.reusable_points
    item.extraction_model = result.model
    item.extracted_at = datetime.now(UTC)
    metadata = dict(item.source_metadata or {})
    metadata["extraction"] = {
        "provider": result.provider,
        "model": result.model,
        "usage": result.usage,
        "extracted_at": item.extracted_at.isoformat(),
    }
    item.source_metadata = metadata


def _knowledge_prompt(item: KnowledgeItem) -> str:
    authors = ", ".join(item.authors or []) or "unknown"
    metadata = json.dumps(item.source_metadata or {}, ensure_ascii=False, sort_keys=True)
    return "\n".join(
        [
            f"Title: {item.title}",
            f"Authors: {authors}",
            f"Abstract: {item.abstract or 'No abstract provided.'}",
            f"Source URL: {item.url}",
            f"Code repository: {item.code_repository_url or 'None'}",
            f"Source metadata: {metadata}",
            "",
            "Extract:",
            "1. A compact summary.",
            "2. Method or approach details.",
            "3. Main contributions.",
            "4. Reusable points for future experiments or papers.",
        ]
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionAgentError("extraction model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ExtractionAgentError("extraction model response was not a JSON object")
    return payload


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ExtractionAgentError(f"extraction response field {field_name} must be text")
    text = " ".join(value.split())
    if not text:
        raise ExtractionAgentError(f"extraction response field {field_name} must not be empty")
    return text


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExtractionAgentError("extraction list fields must be arrays")
    texts: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ExtractionAgentError("extraction list fields must contain strings")
        text = " ".join(item.split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            texts.append(text)
    return texts
