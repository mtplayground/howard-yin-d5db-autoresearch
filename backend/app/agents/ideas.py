from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Idea, IdeaStatus, KnowledgeItem
from app.services.model_adapter import ModelAdapter, ModelAdapterError, ModelMessage, ModelRequest, build_model_adapter
from app.services.model_settings import load_effective_model_settings


class IdeaGenerationAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedIdea:
    title: str
    problem_statement: str
    hypothesis: str
    motivation: str
    related_work: list[str] = field(default_factory=list)
    feasibility: str = ""
    score: float | None = None
    reusable_points: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IdeaGenerationResult:
    ideas: list[GeneratedIdea]
    model: str
    provider: str
    usage: dict[str, Any] = field(default_factory=dict)


class IdeaGenerationAgent:
    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    async def generate(self, knowledge_items: Sequence[KnowledgeItem], *, max_ideas: int = 5) -> IdeaGenerationResult:
        if max_ideas < 1:
            raise IdeaGenerationAgentError("max_ideas must be at least 1")
        if not knowledge_items:
            raise IdeaGenerationAgentError("at least one knowledge item is required")

        try:
            response = await self._adapter.complete(
                ModelRequest(
                    messages=[
                        ModelMessage(
                            role="system",
                            content=(
                                "You generate candidate research ideas from structured knowledge. Return strict JSON "
                                "only with an ideas array. Each idea must include title, problem_statement, hypothesis, "
                                "motivation, related_work, feasibility, score, and reusable_points."
                            ),
                        ),
                        ModelMessage(role="user", content=_generation_prompt(knowledge_items, max_ideas=max_ideas)),
                    ],
                    temperature=0.35,
                    max_tokens=1600,
                )
            )
        except ModelAdapterError as exc:
            raise IdeaGenerationAgentError(str(exc)) from exc

        payload = _parse_json_object(response.content)
        ideas_payload = payload.get("ideas")
        if not isinstance(ideas_payload, list) or not ideas_payload:
            raise IdeaGenerationAgentError("idea generation response field ideas must be a non-empty array")

        ideas = [_generated_idea_from_payload(raw, index=index) for index, raw in enumerate(ideas_payload[:max_ideas])]
        return IdeaGenerationResult(
            ideas=ideas,
            model=response.model,
            provider=response.provider,
            usage=response.usage,
        )


async def generate_and_persist_ideas(
    db: Session,
    knowledge_item_ids: Sequence[uuid.UUID],
    agent: IdeaGenerationAgent,
    *,
    max_ideas: int = 5,
) -> list[Idea]:
    knowledge_items = _load_knowledge_items(db, knowledge_item_ids)
    result = await agent.generate(knowledge_items, max_ideas=max_ideas)
    created_at = datetime.now(UTC)
    ideas = [_idea_row(generated, knowledge_items, result, created_at=created_at) for generated in result.ideas]
    db.add_all(ideas)
    db.commit()
    for idea in ideas:
        db.refresh(idea)
    return ideas


async def generate_ideas_with_configured_model(
    db: Session,
    settings: Settings,
    knowledge_item_ids: Sequence[uuid.UUID],
    *,
    max_ideas: int = 5,
) -> list[Idea]:
    effective_settings = load_effective_model_settings(db, settings)
    adapter = build_model_adapter(effective_settings, timeout_seconds=settings.model_request_timeout_seconds)
    return await generate_and_persist_ideas(
        db,
        knowledge_item_ids,
        IdeaGenerationAgent(adapter),
        max_ideas=max_ideas,
    )


def _load_knowledge_items(db: Session, knowledge_item_ids: Sequence[uuid.UUID]) -> list[KnowledgeItem]:
    if not knowledge_item_ids:
        raise IdeaGenerationAgentError("at least one knowledge item id is required")

    unique_ids = list(dict.fromkeys(knowledge_item_ids))
    items_by_id = {
        item.id: item
        for item in db.query(KnowledgeItem).filter(KnowledgeItem.id.in_(unique_ids)).all()
    }
    missing_ids = [str(item_id) for item_id in unique_ids if item_id not in items_by_id]
    if missing_ids:
        raise IdeaGenerationAgentError(f"knowledge items were not found: {', '.join(missing_ids)}")
    return [items_by_id[item_id] for item_id in unique_ids]


def _idea_row(
    generated: GeneratedIdea,
    knowledge_items: Sequence[KnowledgeItem],
    result: IdeaGenerationResult,
    *,
    created_at: datetime,
) -> Idea:
    return Idea(
        title=generated.title,
        problem_statement=generated.problem_statement,
        hypothesis=generated.hypothesis,
        status=IdeaStatus.CANDIDATE.value,
        score=generated.score,
        rationale=generated.motivation,
        source_context={
            "knowledge_item_ids": [str(item.id) for item in knowledge_items],
            "knowledge_items": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "source": item.source,
                    "url": item.url,
                    "code_repository_url": item.code_repository_url,
                }
                for item in knowledge_items
            ],
            "related_work": generated.related_work,
        },
        extra={
            "feasibility": generated.feasibility,
            "reusable_points": generated.reusable_points,
            "generation": {
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "generated_at": created_at.isoformat(),
            },
        },
    )


def _generation_prompt(knowledge_items: Sequence[KnowledgeItem], *, max_ideas: int) -> str:
    entries = []
    for index, item in enumerate(knowledge_items, start=1):
        entries.append(
            "\n".join(
                [
                    f"Knowledge item {index}",
                    f"ID: {item.id}",
                    f"Title: {item.title}",
                    f"Summary: {item.summary or item.abstract or 'No summary provided.'}",
                    f"Methods: {json.dumps(item.methods or [], ensure_ascii=False)}",
                    f"Contributions: {json.dumps(item.contributions or [], ensure_ascii=False)}",
                    f"Reusable points: {json.dumps(item.reusable_points or [], ensure_ascii=False)}",
                    f"Source URL: {item.url}",
                    f"Code repository: {item.code_repository_url or 'None'}",
                ]
            )
        )

    return "\n\n".join(
        [
            "Generate candidate research ideas from the structured knowledge below.",
            f"Return at most {max_ideas} ideas.",
            "Each idea should combine or extend the source knowledge and be feasible for an automated experiment.",
            "Return this exact JSON shape:",
            (
                '{"ideas":[{"title":"...","problem_statement":"...","hypothesis":"...",'
                '"motivation":"...","related_work":["..."],"feasibility":"...",'
                '"score":0.0,"reusable_points":["..."]}]}'
            ),
            "",
            "\n\n".join(entries),
        ]
    )


def _generated_idea_from_payload(payload: Any, *, index: int) -> GeneratedIdea:
    if not isinstance(payload, dict):
        raise IdeaGenerationAgentError(f"idea at index {index} must be a JSON object")
    return GeneratedIdea(
        title=_required_text(payload.get("title"), f"ideas[{index}].title", max_length=240),
        problem_statement=_required_text(payload.get("problem_statement"), f"ideas[{index}].problem_statement"),
        hypothesis=_required_text(payload.get("hypothesis"), f"ideas[{index}].hypothesis"),
        motivation=_required_text(payload.get("motivation"), f"ideas[{index}].motivation"),
        related_work=_text_list(payload.get("related_work"), f"ideas[{index}].related_work"),
        feasibility=_required_text(payload.get("feasibility"), f"ideas[{index}].feasibility"),
        score=_optional_score(payload),
        reusable_points=_text_list(payload.get("reusable_points"), f"ideas[{index}].reusable_points"),
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
        raise IdeaGenerationAgentError("idea generation model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise IdeaGenerationAgentError("idea generation model response was not a JSON object")
    return payload


def _required_text(value: Any, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str):
        raise IdeaGenerationAgentError(f"idea generation response field {field_name} must be text")
    text = " ".join(value.split())
    if not text:
        raise IdeaGenerationAgentError(f"idea generation response field {field_name} must not be empty")
    if max_length is not None and len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise IdeaGenerationAgentError(f"idea generation response field {field_name} must be an array")
    texts: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise IdeaGenerationAgentError(f"idea generation response field {field_name} must contain strings")
        text = " ".join(item.split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            texts.append(text)
    return texts


def _optional_score(payload: dict[str, Any]) -> float | None:
    value = payload.get("score", payload.get("feasibility_score", payload.get("confidence")))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise IdeaGenerationAgentError("idea generation score must be numeric")
    try:
        score = float(value)
    except ValueError as exc:
        raise IdeaGenerationAgentError("idea generation score must be numeric") from exc
    return min(max(score, 0.0), 1.0)
