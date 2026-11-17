from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DestinationChannel(TimestampMixin, Base):
    __tablename__ = "destination_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    google_identity: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    oauth_status: Mapped[str] = mapped_column(String(32), default="not_connected", nullable=False)
    operational_state: Mapped[str] = mapped_column(String(32), default="paused", nullable=False)
    default_privacy: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    daily_upload_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    upload_interval_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    uploads_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    google_connection: Mapped[GoogleConnection | None] = relationship(
        back_populates="destination_channel", uselist=False, cascade="all, delete-orphan"
    )
    pending_oauth_candidate: Mapped[OAuthChannelCandidate | None] = relationship(
        back_populates="destination_channel", uselist=False, cascade="all, delete-orphan"
    )


class GoogleConnection(TimestampMixin, Base):
    __tablename__ = "google_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    destination_channel_id: Mapped[str] = mapped_column(
        ForeignKey("destination_channels.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    google_subject: Mapped[str | None] = mapped_column(String(255))
    google_email: Mapped[str | None] = mapped_column(String(255))
    authorized_channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_title: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_thumbnail_url: Mapped[str | None] = mapped_column(Text)
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="connected", nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    destination_channel: Mapped[DestinationChannel] = relationship(back_populates="google_connection")


class OAuthAuthorizationState(Base):
    __tablename__ = "oauth_authorization_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    destination_channel_id: Mapped[str] = mapped_column(
        ForeignKey("destination_channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code_verifier_ciphertext: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OAuthChannelCandidate(Base):
    __tablename__ = "oauth_channel_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    destination_channel_id: Mapped[str] = mapped_column(
        ForeignKey("destination_channels.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    google_subject: Mapped[str | None] = mapped_column(String(255))
    google_email: Mapped[str | None] = mapped_column(String(255))
    authorized_channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_title: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_thumbnail_url: Mapped[str | None] = mapped_column(Text)
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    destination_channel: Mapped[DestinationChannel] = relationship(back_populates="pending_oauth_candidate")


class PipelineVersion(Base):
    __tablename__ = "pipeline_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    settings_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SourceFeed(TimestampMixin, Base):
    __tablename__ = "source_feeds"
    __table_args__ = (
        UniqueConstraint(
            "destination_channel_id",
            "source_channel_url",
            name="uq_source_feeds_destination_url",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    destination_channel_id: Mapped[str] = mapped_column(
        ForeignKey("destination_channels.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    source_channel_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_channel_title: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32), default="syncing", nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    latest_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("url_batches.id", ondelete="SET NULL")
    )
    discovered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queued_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    destination_channel: Mapped[DestinationChannel] = relationship()


class UrlBatch(Base):
    __tablename__ = "url_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    destination_channel_id: Mapped[str] = mapped_column(
        ForeignKey("destination_channels.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    pipeline_version_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_versions.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    processing_state: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    destination_channel: Mapped[DestinationChannel] = relationship()
    pipeline_version: Mapped[PipelineVersion] = relationship()
    items: Mapped[list[BatchItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="BatchItem.position"
    )


class BatchItem(Base):
    __tablename__ = "batch_items"
    __table_args__ = (
        Index("ix_batch_items_batch_position", "batch_id", "position", unique=True),
        Index(
            "uq_batch_items_delivery_identity",
            "source_provider",
            "source_video_id",
            "destination_channel_id",
            unique=True,
            postgresql_where=text("validation_status = 'accepted'"),
            sqlite_where=text("validation_status = 'accepted'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("url_batches.id", ondelete="CASCADE"), nullable=False)
    destination_channel_id: Mapped[str] = mapped_column(
        ForeignKey("destination_channels.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_provider: Mapped[str] = mapped_column(String(32), default="youtube", nullable=False)
    source_video_id: Mapped[str | None] = mapped_column(String(64), index=True)
    normalized_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    validation_status: Mapped[str] = mapped_column(String(24), nullable=False)
    validation_message: Mapped[str | None] = mapped_column(Text)
    duplicate_of_item_id: Mapped[str | None] = mapped_column(ForeignKey("batch_items.id"))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    batch: Mapped[UrlBatch] = relationship(back_populates="items")
    render_job: Mapped[RenderJob | None] = relationship(back_populates="batch_item", uselist=False)
    publish_job: Mapped[PublishJob | None] = relationship(back_populates="batch_item", uselist=False)
    media_assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="batch_item", cascade="all, delete-orphan", order_by="MediaAsset.created_at"
    )


class MediaAsset(TimestampMixin, Base):
    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("batch_item_id", "kind", name="uq_media_assets_item_kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_item_id: Mapped[str] = mapped_column(ForeignKey("batch_items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    metadata_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retained_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch_item: Mapped[BatchItem] = relationship(back_populates="media_assets")


class RenderJob(TimestampMixin, Base):
    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_item_id: Mapped[str] = mapped_column(
        ForeignKey("batch_items.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    pipeline_version_id: Mapped[str] = mapped_column(ForeignKey("pipeline_versions.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="waiting_for_source", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)

    batch_item: Mapped[BatchItem] = relationship(back_populates="render_job")


class PublishJob(TimestampMixin, Base):
    __tablename__ = "publish_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_item_id: Mapped[str] = mapped_column(
        ForeignKey("batch_items.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    destination_channel_id: Mapped[str] = mapped_column(
        ForeignKey("destination_channels.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    state: Mapped[str] = mapped_column(String(40), default="waiting_for_render", nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    youtube_video_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)

    batch_item: Mapped[BatchItem] = relationship(back_populates="publish_job")
    upload_session: Mapped[UploadSession | None] = relationship(back_populates="publish_job", uselist=False)


class UploadSession(TimestampMixin, Base):
    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    publish_job_id: Mapped[str] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), default="not_started", nullable=False)
    session_url_ciphertext: Mapped[str | None] = mapped_column(Text)
    acknowledged_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    publish_job: Mapped[PublishJob] = relationship(back_populates="upload_session")


class AutomationEvent(Base):
    __tablename__ = "automation_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    aggregate_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    batch_item_id: Mapped[str | None] = mapped_column(ForeignKey("batch_items.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(40))
    to_state: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
