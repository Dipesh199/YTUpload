"""make automation media assets idempotent

Revision ID: 20260928_0002
Revises: 20260928_0001
Create Date: 2026-09-28
"""
from alembic import op


revision = "20260928_0002"
down_revision = "20260928_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.create_unique_constraint(
            "uq_media_assets_item_kind",
            ["batch_item_id", "kind"],
        )


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.drop_constraint("uq_media_assets_item_kind", type_="unique")
