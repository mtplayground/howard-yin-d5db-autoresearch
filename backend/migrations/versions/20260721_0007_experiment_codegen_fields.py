"""add experiment codegen fields

Revision ID: 20260721_0007
Revises: 20260721_0006
Create Date: 2026-07-21 00:28:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260721_0007"
down_revision = "20260721_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("code_files", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "experiments",
        sa.Column("dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "experiments",
        sa.Column("run_command", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("experiments", sa.Column("codegen_model", sa.String(length=160), nullable=True))
    op.add_column("experiments", sa.Column("code_generated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("experiments", "code_generated_at")
    op.drop_column("experiments", "codegen_model")
    op.drop_column("experiments", "run_command")
    op.drop_column("experiments", "dependencies")
    op.drop_column("experiments", "code_files")
