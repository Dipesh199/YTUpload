"""add upload cadence and durable source metadata

Revision ID: 20260928_0003
Revises: 20260928_0002
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "20260928_0003"
down_revision = "20260928_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("destination_channels") as batch_op:
        batch_op.add_column(sa.Column("upload_interval_minutes", sa.Integer(), nullable=False, server_default="15"))
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.add_column(sa.Column("metadata_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.drop_column("metadata_snapshot")
    with op.batch_alter_table("destination_channels") as batch_op:
        batch_op.drop_column("upload_interval_minutes")
