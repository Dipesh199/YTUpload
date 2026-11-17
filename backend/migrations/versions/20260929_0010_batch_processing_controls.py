"""add durable batch processing controls

Revision ID: 20260929_0010
Revises: 20260929_0009
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "20260929_0010"
down_revision = "20260929_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "url_batches",
        sa.Column("processing_state", sa.String(24), nullable=False, server_default="running"),
    )
    op.alter_column("url_batches", "processing_state", server_default=None)


def downgrade() -> None:
    op.drop_column("url_batches", "processing_state")
