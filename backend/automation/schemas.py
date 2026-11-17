from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ChannelCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    youtube_channel_id: str = Field(min_length=3, max_length=64)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    daily_upload_limit: int = Field(default=5, ge=1, le=100)
    upload_interval_minutes: int = Field(default=15, ge=5, le=1440)

    @field_validator("youtube_channel_id")
    @classmethod
    def clean_channel_id(cls, value: str) -> str:
        cleaned = value.strip()
        if any(character.isspace() for character in cleaned):
            raise ValueError("Channel ID cannot contain spaces")
        return cleaned


class UrlPreviewRequest(BaseModel):
    destination_channel_id: str
    urls: str = Field(min_length=1, max_length=100_000)


class BatchSettings(BaseModel):
    template_id: str = Field(min_length=1, max_length=120)
    output_profile: str = Field(default="youtube-1080p", max_length=64)
    metadata_mode: Literal["preserve", "template", "rewrite", "manual"] = "preserve"
    visibility: Literal["private", "unlisted", "public", "scheduled"] = "public"
    scheduled_for: datetime | None = None
    schedule_interval_minutes: int = Field(default=30, ge=5, le=1440)
    playlist_ids: list[str] = Field(default_factory=list, max_length=20)
    thumbnail_mode: Literal["source", "generated", "custom", "none"] = "source"
    delete_source_after_success: bool = True
    export_retention_days: int = Field(default=14, ge=1, le=365)
    notify_subscribers: bool = False

    @model_validator(mode="after")
    def validate_schedule(self) -> "BatchSettings":
        if self.visibility == "scheduled" and self.scheduled_for is None:
            raise ValueError("A publication date and time is required for scheduled visibility")
        if self.output_profile == "youtube-shorts":
            self.thumbnail_mode = "none"
        return self


class BatchCreate(BaseModel):
    destination_channel_id: str
    name: str = Field(min_length=1, max_length=140)
    urls: str = Field(min_length=1, max_length=1_000_000)
    settings: BatchSettings


class BatchScheduleRequest(BaseModel):
    first_publication_at: datetime | None = None
    schedule_interval_minutes: int = Field(default=30, ge=5, le=1440)


class SourceFeedCreate(BaseModel):
    destination_channel_id: str
    name: str = Field(min_length=1, max_length=140)
    source_channel_url: str = Field(min_length=1, max_length=500)
    settings: BatchSettings


class AutomationEditRequest(BaseModel):
    settings: dict[str, Any]
