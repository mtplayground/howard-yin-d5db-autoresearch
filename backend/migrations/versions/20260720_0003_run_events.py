"""create run events table

Revision ID: 20260720_0003
Revises: 20260720_0002
Create Date: 2026-07-20 23:45:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260720_0003"
down_revision = "20260720_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("stage", sa.String(length=120), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_events_event_type_created_at", "run_events", ["event_type", "created_at"])
    op.create_index("ix_run_events_run_id_created_at", "run_events", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_run_events_run_id_created_at", table_name="run_events")
    op.drop_index("ix_run_events_event_type_created_at", table_name="run_events")
    op.drop_table("run_events")
