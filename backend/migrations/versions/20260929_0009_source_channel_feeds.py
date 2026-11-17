"""add durable source channel feeds

Revision ID: 20260929_0009
Revises: 20260928_0008
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "20260929_0009"
down_revision = "20260928_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_feeds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "destination_channel_id",
            sa.String(36),
            sa.ForeignKey("destination_channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("source_channel_url", sa.Text(), nullable=False),
        sa.Column("source_channel_id", sa.String(64)),
        sa.Column("source_channel_title", sa.String(255)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("settings_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "latest_batch_id",
            sa.String(36),
            sa.ForeignKey("url_batches.id", ondelete="SET NULL"),
        ),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("queued_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "destination_channel_id",
            "source_channel_url",
            name="uq_source_feeds_destination_url",
        ),
    )
    op.create_index(
        "ix_source_feeds_destination_channel_id",
        "source_feeds",
        ["destination_channel_id"],
    )
    op.create_index(
        "ix_source_feeds_source_channel_id",
        "source_feeds",
        ["source_channel_id"],
    )


def downgrade() -> None:
    op.drop_table("source_feeds")
