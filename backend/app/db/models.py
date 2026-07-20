from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IdeaStatus(str, enum.Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ExperimentStatus(str, enum.Enum):
    PLANNED = "planned"
    GENERATING = "generating"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class PaperStatus(str, enum.Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    COMPILED = "compiled"
    FAILED = "failed"


class ArtifactKind(str, enum.Enum):
    DATASET = "dataset"
    SOURCE_CODE = "source_code"
    LOG = "log"
    RESULT = "result"
    FIGURE = "figure"
    LATEX = "latex"
    PDF = "pdf"
    OTHER = "other"


class ModelSettings(Base, TimestampMixin):
    __tablename__ = "model_settings"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    default_model: Mapped[str] = mapped_column(String(160), nullable=False)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("id = 1", name="ck_model_settings_singleton"),)


class Idea(Base, TimestampMixin):
    __tablename__ = "ideas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    problem_statement: Mapped[str | None] = mapped_column(Text)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=IdeaStatus.DRAFT.value)
    score: Mapped[float | None] = mapped_column(Numeric(8, 4))
    rationale: Mapped[str | None] = mapped_column(Text)
    source_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    runs: Mapped[list[Run]] = relationship(back_populates="idea")
    experiments: Mapped[list[Experiment]] = relationship(back_populates="idea")
    papers: Mapped[list[Paper]] = relationship(back_populates="idea")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="idea")

    __table_args__ = (
        Index("ix_ideas_status_created_at", "status", "created_at"),
        Index("ix_ideas_score", "score"),
    )


class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    idea_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ideas.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.QUEUED.value)
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(120))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    idea: Mapped[Idea | None] = relationship(back_populates="runs")
    experiments: Mapped[list[Experiment]] = relationship(back_populates="run")
    papers: Mapped[list[Paper]] = relationship(back_populates="run")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="run")
    events: Mapped[list[RunEvent]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_runs_status_created_at", "status", "created_at"),
        Index("ix_runs_idea_id", "idea_id"),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run: Mapped[Run] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_run_events_run_id_created_at", "run_id", "created_at"),
        Index("ix_run_events_event_type_created_at", "event_type", "created_at"),
    )


class Experiment(Base, TimestampMixin):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    idea_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ideas.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ExperimentStatus.PLANNED.value)
    sandbox_image: Mapped[str | None] = mapped_column(String(240))
    code_storage_key: Mapped[str | None] = mapped_column(String(512))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run | None] = relationship(back_populates="experiments")
    idea: Mapped[Idea | None] = relationship(back_populates="experiments")
    papers: Mapped[list[Paper]] = relationship(back_populates="experiment")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="experiment")

    __table_args__ = (
        Index("ix_experiments_status_created_at", "status", "created_at"),
        Index("ix_experiments_run_id", "run_id"),
        Index("ix_experiments_idea_id", "idea_id"),
    )


class Paper(Base, TimestampMixin):
    __tablename__ = "papers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    idea_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ideas.id", ondelete="SET NULL"))
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiments.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PaperStatus.DRAFT.value)
    latex_storage_key: Mapped[str | None] = mapped_column(String(512))
    pdf_storage_key: Mapped[str | None] = mapped_column(String(512))
    bibliography: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    review_notes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    compiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run | None] = relationship(back_populates="papers")
    idea: Mapped[Idea | None] = relationship(back_populates="papers")
    experiment: Mapped[Experiment | None] = relationship(back_populates="papers")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="paper")

    __table_args__ = (
        Index("ix_papers_status_created_at", "status", "created_at"),
        Index("ix_papers_run_id", "run_id"),
        Index("ix_papers_idea_id", "idea_id"),
        Index("ix_papers_experiment_id", "experiment_id"),
    )


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    idea_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ideas.id", ondelete="SET NULL"))
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiments.id", ondelete="SET NULL"))
    paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default=ArtifactKind.OTHER.value)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(260))
    content_type: Mapped[str | None] = mapped_column(String(160))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    run: Mapped[Run | None] = relationship(back_populates="artifacts")
    idea: Mapped[Idea | None] = relationship(back_populates="artifacts")
    experiment: Mapped[Experiment | None] = relationship(back_populates="artifacts")
    paper: Mapped[Paper | None] = relationship(back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
        Index("ix_artifacts_kind_created_at", "kind", "created_at"),
        Index("ix_artifacts_run_id", "run_id"),
        Index("ix_artifacts_idea_id", "idea_id"),
        Index("ix_artifacts_experiment_id", "experiment_id"),
        Index("ix_artifacts_paper_id", "paper_id"),
    )
