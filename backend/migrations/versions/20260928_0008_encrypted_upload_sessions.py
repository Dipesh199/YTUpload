"""encrypt persisted YouTube resumable upload sessions

Revision ID: 20260928_0008
Revises: 20260928_0007
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "20260928_0008"
down_revision = "20260928_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.add_column(sa.Column("session_url_ciphertext", sa.Text(), nullable=True))
        batch_op.drop_column("session_url")


def downgrade() -> None:
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.add_column(sa.Column("session_url", sa.Text(), nullable=True))
        batch_op.drop_column("session_url_ciphertext")
