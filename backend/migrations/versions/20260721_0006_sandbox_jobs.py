"""create sandbox jobs table

Revision ID: 20260721_0006
Revises: 20260720_0005
Create Date: 2026-07-21 00:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260721_0006"
down_revision = "20260720_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sandbox_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("stdin", sa.Text(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.SmallInteger(), nullable=True),
        sa.Column("timeout_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("cpu_time_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sandbox_jobs_experiment_id", "sandbox_jobs", ["experiment_id"])
    op.create_index("ix_sandbox_jobs_run_id", "sandbox_jobs", ["run_id"])
    op.create_index("ix_sandbox_jobs_status_created_at", "sandbox_jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_sandbox_jobs_status_created_at", table_name="sandbox_jobs")
    op.drop_index("ix_sandbox_jobs_run_id", table_name="sandbox_jobs")
    op.drop_index("ix_sandbox_jobs_experiment_id", table_name="sandbox_jobs")
    op.drop_table("sandbox_jobs")
