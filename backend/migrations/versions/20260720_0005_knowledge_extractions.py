"""add knowledge extraction fields

Revision ID: 20260720_0005
Revises: 20260720_0004
Create Date: 2026-07-21 00:05:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260720_0005"
down_revision = "20260720_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_items", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("knowledge_items", sa.Column("methods", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("knowledge_items", sa.Column("contributions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("knowledge_items", sa.Column("reusable_points", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("knowledge_items", sa.Column("extraction_model", sa.String(length=160), nullable=True))
    op.add_column("knowledge_items", sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_items", "extracted_at")
    op.drop_column("knowledge_items", "extraction_model")
    op.drop_column("knowledge_items", "reusable_points")
    op.drop_column("knowledge_items", "contributions")
    op.drop_column("knowledge_items", "methods")
    op.drop_column("knowledge_items", "summary")
