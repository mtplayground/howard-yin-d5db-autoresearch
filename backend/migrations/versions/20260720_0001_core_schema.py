"""create core research schema

Revision ID: 20260720_0001
Revises:
Create Date: 2026-07-20 23:25:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260720_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "ideas",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("problem_statement", sa.Text(), nullable=True),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("score", sa.Numeric(8, 4), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("source_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'candidate', 'approved', 'rejected', 'archived')", name="ck_ideas_status"),
        sa.CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="ck_ideas_score_range"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ideas_score", "ideas", ["score"])
    op.create_index("ix_ideas_status_created_at", "ideas", ["status", "created_at"])

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("trigger_source", sa.String(length=64), nullable=False),
        sa.Column("current_stage", sa.String(length=120), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')", name="ck_runs_status"),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_idea_id", "runs", ["idea_id"])
    op.create_index("ix_runs_status_created_at", "runs", ["status", "created_at"])

    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("sandbox_image", sa.String(length=240), nullable=True),
        sa.Column("code_storage_key", sa.String(length=512), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned', 'generating', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_experiments_status",
        ),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiments_idea_id", "experiments", ["idea_id"])
    op.create_index("ix_experiments_run_id", "experiments", ["run_id"])
    op.create_index("ix_experiments_status_created_at", "experiments", ["status", "created_at"])

    op.create_table(
        "papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("latex_storage_key", sa.String(length=512), nullable=True),
        sa.Column("pdf_storage_key", sa.String(length=512), nullable=True),
        sa.Column("bibliography", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("review_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'generating', 'reviewing', 'compiled', 'failed')", name="ck_papers_status"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_papers_experiment_id", "papers", ["experiment_id"])
    op.create_index("ix_papers_idea_id", "papers", ["idea_id"])
    op.create_index("ix_papers_run_id", "papers", ["run_id"])
    op.create_index("ix_papers_status_created_at", "papers", ["status", "created_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False, server_default="other"),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("filename", sa.String(length=260), nullable=True),
        sa.Column("content_type", sa.String(length=160), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('dataset', 'source_code', 'log', 'result', 'figure', 'latex', 'pdf', 'other')",
            name="ck_artifacts_kind",
        ),
        sa.CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_artifacts_byte_size_nonnegative"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
    )
    op.create_index("ix_artifacts_experiment_id", "artifacts", ["experiment_id"])
    op.create_index("ix_artifacts_idea_id", "artifacts", ["idea_id"])
    op.create_index("ix_artifacts_kind_created_at", "artifacts", ["kind", "created_at"])
    op.create_index("ix_artifacts_paper_id", "artifacts", ["paper_id"])
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_index("ix_artifacts_paper_id", table_name="artifacts")
    op.drop_index("ix_artifacts_kind_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_idea_id", table_name="artifacts")
    op.drop_index("ix_artifacts_experiment_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_papers_status_created_at", table_name="papers")
    op.drop_index("ix_papers_run_id", table_name="papers")
    op.drop_index("ix_papers_idea_id", table_name="papers")
    op.drop_index("ix_papers_experiment_id", table_name="papers")
    op.drop_table("papers")

    op.drop_index("ix_experiments_status_created_at", table_name="experiments")
    op.drop_index("ix_experiments_run_id", table_name="experiments")
    op.drop_index("ix_experiments_idea_id", table_name="experiments")
    op.drop_table("experiments")

    op.drop_index("ix_runs_status_created_at", table_name="runs")
    op.drop_index("ix_runs_idea_id", table_name="runs")
    op.drop_table("runs")

    op.drop_index("ix_ideas_status_created_at", table_name="ideas")
    op.drop_index("ix_ideas_score", table_name="ideas")
    op.drop_table("ideas")
