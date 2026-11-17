"""automation control-plane foundation

Revision ID: 20260928_0001
Revises:
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "20260928_0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "destination_channels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("youtube_channel_id", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("google_identity", sa.String(255)),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("oauth_status", sa.String(32), nullable=False),
        sa.Column("operational_state", sa.String(32), nullable=False),
        sa.Column("default_privacy", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("daily_upload_limit", sa.Integer(), nullable=False),
        sa.Column("uploads_today", sa.Integer(), nullable=False),
        sa.Column("next_publish_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_table(
        "pipeline_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("settings_snapshot", sa.JSON(), nullable=False),
        sa.Column("settings_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pipeline_versions_settings_hash", "pipeline_versions", ["settings_hash"])
    op.create_table(
        "url_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("destination_channel_id", sa.String(36), sa.ForeignKey("destination_channels.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pipeline_version_id", sa.String(36), sa.ForeignKey("pipeline_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("settings_snapshot", sa.JSON(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("invalid_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_url_batches_destination_channel_id", "url_batches", ["destination_channel_id"])
    op.create_table(
        "batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("url_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_channel_id", sa.String(36), sa.ForeignKey("destination_channels.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("raw_url", sa.Text(), nullable=False),
        sa.Column("source_provider", sa.String(32), nullable=False),
        sa.Column("source_video_id", sa.String(64)),
        sa.Column("normalized_url", sa.Text()),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column("source_title", sa.Text()),
        sa.Column("validation_status", sa.String(24), nullable=False),
        sa.Column("validation_message", sa.Text()),
        sa.Column("duplicate_of_item_id", sa.String(36), sa.ForeignKey("batch_items.id")),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batch_items_batch_position", "batch_items", ["batch_id", "position"], unique=True)
    op.create_index("ix_batch_items_destination_channel_id", "batch_items", ["destination_channel_id"])
    op.create_index("ix_batch_items_source_video_id", "batch_items", ["source_video_id"])
    op.create_index(
        "uq_batch_items_delivery_identity", "batch_items",
        ["source_provider", "source_video_id", "destination_channel_id"], unique=True,
        postgresql_where=sa.text("validation_status = 'accepted'"),
    )
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_item_id", sa.String(36), sa.ForeignKey("batch_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("storage_path", sa.Text()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("retained_until", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_media_assets_batch_item_id", "media_assets", ["batch_item_id"])
    op.create_table(
        "render_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_item_id", sa.String(36), sa.ForeignKey("batch_items.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("pipeline_version_id", sa.String(36), sa.ForeignKey("pipeline_versions.id"), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("lease_owner", sa.String(120)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        *timestamps(),
    )
    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_item_id", sa.String(36), sa.ForeignKey("batch_items.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("destination_channel_id", sa.String(36), sa.ForeignKey("destination_channels.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("youtube_video_id", sa.String(64), unique=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column("lease_owner", sa.String(120)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        *timestamps(),
    )
    op.create_index("ix_publish_jobs_destination_channel_id", "publish_jobs", ["destination_channel_id"])
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("publish_job_id", sa.String(36), sa.ForeignKey("publish_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("session_url", sa.Text()),
        sa.Column("acknowledged_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        *timestamps(),
    )
    op.create_table(
        "automation_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("aggregate_type", sa.String(40), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("batch_item_id", sa.String(36), sa.ForeignKey("batch_items.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("from_state", sa.String(40)),
        sa.Column("to_state", sa.String(40)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_automation_events_aggregate_type", "automation_events", ["aggregate_type"])
    op.create_index("ix_automation_events_aggregate_id", "automation_events", ["aggregate_id"])
    op.create_index("ix_automation_events_occurred_at", "automation_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("automation_events")
    op.drop_table("upload_sessions")
    op.drop_table("publish_jobs")
    op.drop_table("render_jobs")
    op.drop_table("media_assets")
    op.drop_table("batch_items")
    op.drop_table("url_batches")
    op.drop_table("pipeline_versions")
    op.drop_table("destination_channels")

