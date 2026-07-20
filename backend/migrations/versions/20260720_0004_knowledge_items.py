"""create knowledge items table

Revision ID: 20260720_0004
Revises: 20260720_0003
Create Date: 2026-07-20 23:55:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260720_0004"
down_revision = "20260720_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("canonical_key", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("code_repository_url", sa.Text(), nullable=True),
        sa.Column("authors", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key", name="uq_knowledge_items_canonical_key"),
        sa.UniqueConstraint("source", "source_id", name="uq_knowledge_items_source_source_id"),
    )
    op.create_index("ix_knowledge_items_published_at", "knowledge_items", ["published_at"])
    op.create_index("ix_knowledge_items_source_created_at", "knowledge_items", ["source", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_items_source_created_at", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_published_at", table_name="knowledge_items")
    op.drop_table("knowledge_items")
