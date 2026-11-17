"""add safe destination channel archival

Revision ID: 20260928_0007
Revises: 20260928_0006
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "20260928_0007"
down_revision = "20260928_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("destination_channels") as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("destination_channels") as batch_op:
        batch_op.drop_column("archived_at")
