from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from .models import (
    AutomationEvent,
    BatchItem,
    DestinationChannel,
    MediaAsset,
    OAuthAuthorizationState,
    PipelineVersion,
    PublishJob,
    RenderJob,
    SourceFeed,
    UploadSession,
    UrlBatch,
    utcnow,
)
from .schemas import AutomationEditRequest, BatchCreate, BatchScheduleRequest, ChannelCreate, SourceFeedCreate
from .source_feeds import DiscoveredChannel, discover_source_channel, normalize_source_channel_url
from .urls import parse_youtube_url, split_url_input
from .oauth import (
    candidate_dict,
    cleanup_expired_oauth_candidates,
    connection_dict,
    discard_oauth_candidate,
    oauth_configuration,
)


OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/app/output"))


def channel_dict(channel: DestinationChannel) -> dict[str, Any]:
    return {
        "id": channel.id,
        "youtube_channel_id": channel.youtube_channel_id,
        "display_name": channel.display_name,
        "google_identity": channel.google_identity,
        "avatar_url": channel.avatar_url,
        "oauth_status": channel.oauth_status,
        "operational_state": channel.operational_state,
        "default_privacy": channel.default_privacy,
        "timezone": channel.timezone,
        "daily_upload_limit": channel.daily_upload_limit,
        "upload_interval_minutes": channel.upload_interval_minutes,
        "uploads_today": channel.uploads_today,
        "next_publish_at": channel.next_publish_at,
        "archived_at": channel.archived_at,
        "created_at": channel.created_at,
        "google_connection": connection_dict(channel.google_connection),
        "pending_oauth_candidate": candidate_dict(channel.pending_oauth_candidate),
    }


def list_channels(db: Session) -> list[dict[str, Any]]:
    cleanup_expired_oauth_candidates(db)
    channels = db.scalars(select(DestinationChannel).where(
        DestinationChannel.archived_at.is_(None)
    ).order_by(DestinationChannel.created_at)).all()
    results: list[dict[str, Any]] = []
    for channel in channels:
        result = channel_dict(channel)
        result["last_oauth_mismatch"] = None
        if channel.oauth_status == "channel_mismatch" and not channel.pending_oauth_candidate:
            event = db.scalar(select(AutomationEvent).where(
                AutomationEvent.aggregate_id == channel.id,
                AutomationEvent.event_type == "destination_channel.oauth_channel_mismatch",
            ).order_by(AutomationEvent.occurred_at.desc()).limit(1))
            if event:
                result["last_oauth_mismatch"] = {
                    "authorized_channel_id": event.details.get("authorized_channel_id"),
                    "channel_title": event.details.get("authorized_channel_title"),
                    "discovered_at": event.occurred_at,
                }
        results.append(result)
    return results


def create_channel(db: Session, payload: ChannelCreate) -> dict[str, Any]:
    existing = db.scalar(
        select(DestinationChannel).where(DestinationChannel.youtube_channel_id == payload.youtube_channel_id)
    )
    if existing:
        raise HTTPException(409, "That destination channel is already registered")
    channel = DestinationChannel(
        display_name=payload.display_name.strip(),
        youtube_channel_id=payload.youtube_channel_id,
        timezone=payload.timezone,
        daily_upload_limit=payload.daily_upload_limit,
        upload_interval_minutes=payload.upload_interval_minutes,
        oauth_status="not_connected",
        operational_state="paused",
    )
    db.add(channel)
    db.flush()
    db.add(AutomationEvent(
        aggregate_type="destination_channel",
        aggregate_id=channel.id,
        event_type="destination_channel.registered",
        to_state="paused",
        details={"oauth_status": "not_connected", "youtube_channel_id": channel.youtube_channel_id},
    ))
    db.commit()
    return channel_dict(channel)


def source_feed_dict(feed: SourceFeed) -> dict[str, Any]:
    return {
        "id": feed.id,
        "name": feed.name,
        "destination_channel_id": feed.destination_channel_id,
        "destination_channel": channel_dict(feed.destination_channel),
        "source_channel_url": feed.source_channel_url,
        "source_channel_id": feed.source_channel_id,
        "source_channel_title": feed.source_channel_title,
        "state": feed.state,
        "settings_snapshot": feed.settings_snapshot,
        "latest_batch_id": feed.latest_batch_id,
        "discovered_count": feed.discovered_count,
        "queued_count": feed.queued_count,
        "duplicate_count": feed.duplicate_count,
        "last_synced_at": feed.last_synced_at,
        "last_error": feed.last_error,
        "created_at": feed.created_at,
        "updated_at": feed.updated_at,
    }


def list_source_feeds(db: Session) -> list[dict[str, Any]]:
    feeds = db.scalars(
        select(SourceFeed)
        .options(selectinload(SourceFeed.destination_channel))
        .order_by(SourceFeed.created_at.desc())
    ).all()
    return [source_feed_dict(feed) for feed in feeds]


def create_source_feed(
    db: Session,
    payload: SourceFeedCreate,
    discoverer: Callable[[str], DiscoveredChannel] = discover_source_channel,
) -> dict[str, Any]:
    channel = db.get(DestinationChannel, payload.destination_channel_id)
    if not channel or channel.archived_at:
        raise HTTPException(404, "Destination channel not found")
    try:
        normalized_url = normalize_source_channel_url(payload.source_channel_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    existing = db.scalar(select(SourceFeed).where(
        SourceFeed.destination_channel_id == channel.id,
        SourceFeed.source_channel_url == normalized_url,
    ))
    if existing:
        raise HTTPException(409, "That source channel is already configured for this destination; sync it again instead")
    settings = payload.settings.model_dump(mode="json")
    if settings["visibility"] != "scheduled":
        settings["visibility"] = "public"
        settings["scheduled_for"] = None
    feed = SourceFeed(
        destination_channel_id=channel.id,
        name=payload.name.strip(),
        source_channel_url=normalized_url,
        state="syncing",
        settings_snapshot=settings,
    )
    db.add(feed)
    db.flush()
    db.add(AutomationEvent(
        aggregate_type="source_feed",
        aggregate_id=feed.id,
        event_type="source_feed.created",
        to_state="syncing",
        details={"destination_channel_id": channel.id, "source_channel_url": normalized_url},
    ))
    db.commit()
    return sync_source_feed(db, feed.id, discoverer)


def sync_source_feed(
    db: Session,
    feed_id: str,
    discoverer: Callable[[str], DiscoveredChannel] = discover_source_channel,
) -> dict[str, Any]:
    feed = db.get(SourceFeed, feed_id)
    if not feed:
        raise HTTPException(404, "Source channel feed not found")
    previous = feed.state
    feed.state = "syncing"
    feed.last_error = None
    db.commit()
    try:
        discovery = discoverer(feed.source_channel_url)
        urls = "\n".join(video.url for video in discovery.videos)
        preview = preview_urls(db, feed.destination_channel_id, urls)
        batch: dict[str, Any] | None = None
        if preview["counts"]["accepted"]:
            batch = create_batch(db, BatchCreate(
                destination_channel_id=feed.destination_channel_id,
                name=f"{feed.name} import {utcnow().strftime('%Y-%m-%d %H:%M')}",
                urls=urls,
                settings=feed.settings_snapshot,
            ))
            titles = {video.video_id: video.title for video in discovery.videos}
            items = db.scalars(select(BatchItem).where(BatchItem.batch_id == batch["id"])).all()
            for item in items:
                if item.source_video_id in titles:
                    item.source_title = titles[item.source_video_id]
            db.commit()
            batch = get_batch(db, batch["id"])
        feed = db.get(SourceFeed, feed_id)
        feed.source_channel_id = discovery.channel_id
        feed.source_channel_title = discovery.title
        feed.state = "active"
        feed.latest_batch_id = batch["id"] if batch else feed.latest_batch_id
        feed.discovered_count = len(discovery.videos)
        feed.queued_count = preview["counts"]["accepted"]
        feed.duplicate_count = preview["counts"]["duplicate"]
        feed.last_synced_at = utcnow()
        feed.last_error = None
        db.add(AutomationEvent(
            aggregate_type="source_feed",
            aggregate_id=feed.id,
            event_type="source_feed.synced",
            from_state=previous,
            to_state="active",
            details={
                "source_channel_id": discovery.channel_id,
                "source_channel_title": discovery.title,
                "discovered_count": len(discovery.videos),
                "queued_count": preview["counts"]["accepted"],
                "duplicate_count": preview["counts"]["duplicate"],
                "batch_id": batch["id"] if batch else None,
            },
        ))
        db.commit()
        db.refresh(feed)
        return {"feed": source_feed_dict(feed), "batch": batch}
    except HTTPException:
        raise
    except Exception as error:
        feed = db.get(SourceFeed, feed_id)
        feed.state = "error"
        feed.last_error = str(error)[-2000:]
        feed.last_synced_at = utcnow()
        db.add(AutomationEvent(
            aggregate_type="source_feed",
            aggregate_id=feed.id,
            event_type="source_feed.sync_failed",
            from_state="syncing",
            to_state="error",
            details={"error": feed.last_error},
        ))
        db.commit()
        raise HTTPException(422, f"Could not fetch that YouTube channel: {feed.last_error}") from error


def archive_channel(db: Session, channel_id: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Destination channel not found")
    if channel.archived_at:
        return {"status": "removed", "channel_id": channel.id}
    connection = channel.google_connection
    if connection and connection.status == "connected":
        raise HTTPException(409, "Disconnect this destination from Google before removing it")
    if channel.pending_oauth_candidate:
        discard_oauth_candidate(db, channel.id, channel.pending_oauth_candidate.id)
        channel = db.get(DestinationChannel, channel_id)

    previous = channel.oauth_status
    db.execute(delete(OAuthAuthorizationState).where(
        OAuthAuthorizationState.destination_channel_id == channel.id
    ))
    blocked = db.execute(update(PublishJob).where(
        PublishJob.destination_channel_id == channel.id,
        PublishJob.state == "waiting_for_upload",
    ).values(state="blocked_not_connected")).rowcount or 0
    batch_count = db.scalar(select(func.count()).select_from(UrlBatch).where(
        UrlBatch.destination_channel_id == channel.id
    )) or 0
    channel.oauth_status = "not_connected"
    channel.operational_state = "disabled"
    channel.next_publish_at = None
    channel.archived_at = utcnow()
    db.add(AutomationEvent(
        aggregate_type="destination_channel",
        aggregate_id=channel.id,
        event_type="destination_channel.removed",
        from_state=previous,
        to_state="disabled",
        details={
            "youtube_channel_id": channel.youtube_channel_id,
            "preserved_batch_count": batch_count,
            "publish_jobs_blocked": blocked,
        },
    ))
    db.commit()
    return {
        "status": "removed",
        "channel_id": channel.id,
        "preserved_batch_count": batch_count,
    }


def _existing_delivery_items(db: Session, destination_channel_id: str, video_ids: list[str]) -> dict[str, BatchItem]:
    if not video_ids:
        return {}
    items = db.scalars(
        select(BatchItem)
        .where(
            BatchItem.destination_channel_id == destination_channel_id,
            BatchItem.source_provider == "youtube",
            BatchItem.source_video_id.in_(video_ids),
            BatchItem.validation_status == "accepted",
        )
        .order_by(BatchItem.created_at)
    ).all()
    return {item.source_video_id: item for item in items if item.source_video_id}


def preview_urls(db: Session, destination_channel_id: str, raw_urls: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, destination_channel_id)
    if not channel or channel.archived_at:
        raise HTTPException(404, "Destination channel not found")
    tokens = split_url_input(raw_urls)
    parsed = [parse_youtube_url(token) for token in tokens]
    existing = _existing_delivery_items(
        db, destination_channel_id, [item.video_id for item in parsed if item.video_id]
    )
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(parsed, 1):
        status = "accepted"
        message = "Ready to add"
        duplicate_item_id = None
        if item.error:
            status, message = "invalid", item.error
        elif item.video_id in seen:
            status, message = "duplicate", "Repeated in this pasted list"
        elif item.video_id in existing:
            status, message = "duplicate", "Already exists for this destination channel"
            duplicate_item_id = existing[item.video_id].id
        if item.video_id:
            seen.add(item.video_id)
        rows.append({
            "position": position,
            "raw_url": item.raw_url,
            "source_video_id": item.video_id,
            "normalized_url": item.normalized_url,
            "thumbnail_url": item.thumbnail_url,
            "status": status,
            "message": message,
            "duplicate_item_id": duplicate_item_id,
        })
    counts = Counter(row["status"] for row in rows)
    return {
        "destination_channel": channel_dict(channel),
        "rows": rows,
        "counts": {"accepted": counts["accepted"], "duplicate": counts["duplicate"], "invalid": counts["invalid"]},
    }


def _settings_snapshot(payload: BatchCreate) -> dict[str, Any]:
    settings = payload.settings.model_dump(mode="json")
    if settings["visibility"] != "scheduled":
        settings["visibility"] = "public"
        settings["scheduled_for"] = None
    return settings


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _scheduled_batch_start(db: Session, payload: BatchCreate) -> datetime | None:
    if payload.settings.visibility != "scheduled" or payload.settings.scheduled_for is None:
        return None
    interval = timedelta(minutes=payload.settings.schedule_interval_minutes)
    requested = _as_utc(payload.settings.scheduled_for)
    start = max(requested, utcnow() + interval)
    latest = db.scalar(select(func.max(PublishJob.scheduled_for)).where(
        PublishJob.destination_channel_id == payload.destination_channel_id,
        PublishJob.visibility == "scheduled",
        PublishJob.scheduled_for.is_not(None),
    ))
    if latest is not None:
        latest = _as_utc(latest)
        if latest >= start:
            start = latest + interval
    return start


def create_batch(db: Session, payload: BatchCreate) -> dict[str, Any]:
    preview = preview_urls(db, payload.destination_channel_id, payload.urls)
    if not preview["counts"]["accepted"]:
        raise HTTPException(422, "No new valid YouTube URLs remain after validation and duplicate detection")
    channel = db.get(DestinationChannel, payload.destination_channel_id)
    settings = _settings_snapshot(payload)
    scheduled_start = _scheduled_batch_start(db, payload)
    if scheduled_start is not None:
        settings["scheduled_for"] = scheduled_start.isoformat()
    settings_json = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    version = PipelineVersion(
        name=f"{payload.name.strip()} settings",
        version_number=1,
        settings_snapshot=settings,
        settings_hash=hashlib.sha256(settings_json.encode()).hexdigest(),
    )
    db.add(version)
    db.flush()
    state = "processing"
    batch = UrlBatch(
        destination_channel_id=channel.id,
        pipeline_version_id=version.id,
        name=payload.name.strip(),
        state=state,
        processing_state="running",
        settings_snapshot=settings,
        accepted_count=preview["counts"]["accepted"],
        duplicate_count=preview["counts"]["duplicate"],
        invalid_count=preview["counts"]["invalid"],
    )
    db.add(batch)
    db.flush()
    accepted_position = 0
    for row in preview["rows"]:
        item_state = "queued" if row["status"] == "accepted" else row["status"]
        item = BatchItem(
            batch_id=batch.id,
            destination_channel_id=channel.id,
            position=row["position"],
            raw_url=row["raw_url"],
            source_video_id=row["source_video_id"],
            normalized_url=row["normalized_url"],
            thumbnail_url=row["thumbnail_url"],
            validation_status=row["status"],
            validation_message=row["message"],
            duplicate_of_item_id=row["duplicate_item_id"],
            state=item_state,
        )
        db.add(item)
        db.flush()
        if row["status"] != "accepted":
            db.add(AutomationEvent(
                aggregate_type="batch_item", aggregate_id=item.id, batch_item_id=item.id,
                event_type=f"batch_item.{row['status']}", to_state=item_state,
                details={"message": row["message"], "source_video_id": row["source_video_id"]},
            ))
            continue
        scheduled_for = None
        if scheduled_start is not None:
            scheduled_for = scheduled_start + timedelta(
                minutes=accepted_position * payload.settings.schedule_interval_minutes
            )
        accepted_position += 1
        delivery_key = f"youtube:{row['source_video_id']}:{channel.id}:{batch.id}"
        render = RenderJob(
            batch_item_id=item.id,
            pipeline_version_id=version.id,
            state="waiting_for_source",
            idempotency_key=f"render:{delivery_key}",
        )
        publish = PublishJob(
            batch_item_id=item.id,
            destination_channel_id=channel.id,
            state="waiting_for_render" if channel.oauth_status == "connected" else "blocked_not_connected",
            visibility=settings["visibility"],
            scheduled_for=scheduled_for,
            idempotency_key=f"publish:{delivery_key}",
        )
        db.add_all([MediaAsset(batch_item_id=item.id, kind="source", state="pending"), render, publish])
        db.flush()
        db.add(UploadSession(publish_job_id=publish.id, state="not_started"))
        db.add(AutomationEvent(
            aggregate_type="batch_item", aggregate_id=item.id, batch_item_id=item.id,
            event_type="batch_item.queued", to_state="queued",
            details={"source_video_id": row["source_video_id"], "publish_state": publish.state},
        ))
    db.add(AutomationEvent(
        aggregate_type="url_batch",
        aggregate_id=batch.id,
        event_type="url_batch.created",
        to_state=state,
        details={
            "accepted_count": batch.accepted_count,
            "duplicate_count": batch.duplicate_count,
            "invalid_count": batch.invalid_count,
            "settings_hash": version.settings_hash,
        },
    ))
    db.commit()
    return get_batch(db, batch.id)


def item_dict(item: BatchItem) -> dict[str, Any]:
    source = next((asset for asset in item.media_assets if asset.kind == "source" and asset.storage_path), None)
    export = next(
        (asset for asset in item.media_assets if asset.kind == "export" and asset.state == "ready"),
        None,
    )
    metadata = export.metadata_snapshot if export else None
    if export and not metadata and export.storage_path:
        try:
            loaded = json.loads((OUTPUT_DIR / f"{export.storage_path}.metadata.json").read_text(encoding="utf-8"))
            metadata = loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError):
            metadata = None
    return {
        "id": item.id,
        "position": item.position,
        "raw_url": item.raw_url,
        "source_video_id": item.source_video_id,
        "source_title": item.source_title,
        "source_description": (metadata or {}).get("description", ""),
        "source_tags": (metadata or {}).get("tags", []),
        "source_asset": None if not source or not source.storage_path else {
            "name": source.storage_path,
            "editor_video_id": hashlib.sha1(source.storage_path.replace("\\", "/").encode()).hexdigest()[:16],
            "state": source.state,
        },
        "normalized_url": item.normalized_url,
        "thumbnail_url": item.thumbnail_url,
        "validation_status": item.validation_status,
        "validation_message": item.validation_message,
        "state": item.state,
        "render_job": None if not item.render_job else {
            "id": item.render_job.id, "state": item.render_job.state,
            "progress": item.render_job.progress, "attempts": item.render_job.attempts,
            "error_code": item.render_job.error_code, "error_message": item.render_job.error_message,
        },
        "publish_job": None if not item.publish_job else {
            "id": item.publish_job.id, "state": item.publish_job.state,
            "progress": item.publish_job.progress, "attempts": item.publish_job.attempts,
            "visibility": item.publish_job.visibility, "scheduled_for": item.publish_job.scheduled_for,
            "youtube_video_id": item.publish_job.youtube_video_id,
            "error_code": item.publish_job.error_code, "error_message": item.publish_job.error_message,
        },
        "output": None if not export or not export.storage_path else {
            "name": export.storage_path,
            "size_bytes": export.size_bytes,
            "retained_until": export.retained_until,
            "url": f"/api/outputs/{export.storage_path}",
        },
    }


def save_automation_edit(db: Session, job_id: str, payload: AutomationEditRequest) -> dict[str, Any]:
    job = db.get(RenderJob, job_id)
    if not job:
        raise HTTPException(404, "Render job not found")
    if job.state in {"acquiring", "downloading", "rendering", "validating"}:
        raise HTTPException(409, "This video is already processing")
    settings = dict(job.batch_item.batch.settings_snapshot)
    settings["template_id"] = "automation-manual-edit"
    settings["output_profile"] = str(payload.settings.get("output_profile", {}).get("id") or settings.get("output_profile") or "youtube-1080p")
    settings["editor_settings"] = payload.settings
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":"), default=str)
    version = PipelineVersion(
        name=f"Manual edit for {job.batch_item.source_title or job.batch_item.source_video_id}",
        version_number=job.attempts + 2,
        settings_snapshot=settings,
        settings_hash=hashlib.sha256(encoded.encode()).hexdigest(),
    )
    db.add(version)
    db.flush()
    previous = job.state
    job.pipeline_version_id = version.id
    job.state = "edit_requested"
    job.progress = 0
    job.error_code = "manual_edit_requested"
    job.error_message = None
    job.batch_item.state = "queued"
    job.batch_item.batch.state = "processing"
    db.add(AutomationEvent(
        aggregate_type="render_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
        event_type="render_job.manual_edit_saved", from_state=previous, to_state="edit_requested",
        details={"pipeline_version_id": version.id, "settings_hash": version.settings_hash},
    ))
    db.commit()
    return {"id": job.id, "state": job.state, "pipeline_version_id": version.id}


def batch_dict(batch: UrlBatch, include_items: bool = True) -> dict[str, Any]:
    result = {
        "id": batch.id,
        "name": batch.name,
        "state": batch.state,
        "processing_state": batch.processing_state,
        "destination_channel": channel_dict(batch.destination_channel),
        "pipeline_version_id": batch.pipeline_version_id,
        "settings_snapshot": batch.settings_snapshot,
        "accepted_count": batch.accepted_count,
        "duplicate_count": batch.duplicate_count,
        "invalid_count": batch.invalid_count,
        "created_at": batch.created_at,
    }
    if include_items:
        result["items"] = [item_dict(item) for item in batch.items]
    return result


def _batch_query():
    return select(UrlBatch).options(
        selectinload(UrlBatch.destination_channel),
        selectinload(UrlBatch.items).selectinload(BatchItem.render_job),
        selectinload(UrlBatch.items).selectinload(BatchItem.publish_job),
        selectinload(UrlBatch.items).selectinload(BatchItem.media_assets),
    )


def get_batch(db: Session, batch_id: str) -> dict[str, Any]:
    batch = db.scalar(_batch_query().where(UrlBatch.id == batch_id))
    if not batch:
        raise HTTPException(404, "URL batch not found")
    return batch_dict(batch)


def list_batches(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    batches = db.scalars(_batch_query().order_by(UrlBatch.created_at.desc()).limit(limit)).all()
    return [batch_dict(batch) for batch in batches]


def set_batch_processing(db: Session, batch_id: str, processing_state: str) -> dict[str, Any]:
    if processing_state not in {"running", "paused"}:
        raise ValueError("Unsupported batch processing state")
    batch = db.scalar(_batch_query().where(UrlBatch.id == batch_id).with_for_update())
    if not batch:
        raise HTTPException(404, "URL batch not found")
    previous = batch.processing_state
    if previous != processing_state:
        batch.processing_state = processing_state
        active_count = sum(
            1 for item in batch.items
            if item.render_job and item.render_job.state in {"acquiring", "downloading", "rendering", "validating"}
        )
        waiting_count = sum(
            1 for item in batch.items
            if item.render_job and item.render_job.state in {"waiting_for_source", "retry_requested", "edit_requested"}
        )
        db.add(AutomationEvent(
            aggregate_type="url_batch",
            aggregate_id=batch.id,
            event_type=f"url_batch.processing_{processing_state}",
            from_state=previous,
            to_state=processing_state,
            details={
                "active_render_count": active_count,
                "waiting_render_count": waiting_count,
                "uploads_continue": True,
            },
        ))
        db.commit()
    return get_batch(db, batch.id)


def schedule_remaining_batch_publications(
    db: Session,
    batch_id: str,
    payload: BatchScheduleRequest,
) -> dict[str, Any]:
    batch = db.scalar(_batch_query().where(UrlBatch.id == batch_id).with_for_update())
    if not batch:
        raise HTTPException(404, "URL batch not found")
    interval = timedelta(minutes=payload.schedule_interval_minutes)
    requested = _as_utc(payload.first_publication_at) if payload.first_publication_at else utcnow() + interval
    start = max(requested, utcnow() + interval)
    latest = db.scalar(select(func.max(PublishJob.scheduled_for)).where(
        PublishJob.destination_channel_id == batch.destination_channel_id,
        PublishJob.visibility == "scheduled",
        PublishJob.scheduled_for.is_not(None),
        PublishJob.batch_item_id.not_in(select(BatchItem.id).where(BatchItem.batch_id == batch.id)),
    ))
    if latest is not None and _as_utc(latest) >= start:
        start = _as_utc(latest) + interval
    eligible_states = {"waiting_for_render", "waiting_for_upload", "blocked_not_connected", "upload_failed"}
    pending = [
        item.publish_job for item in batch.items
        if item.publish_job
        and item.publish_job.youtube_video_id is None
        and item.publish_job.state in eligible_states
    ]
    pending.sort(key=lambda job: job.batch_item.position)
    for index, job in enumerate(pending):
        job.visibility = "scheduled"
        job.scheduled_for = start + index * interval
    settings = dict(batch.settings_snapshot)
    settings.update({
        "visibility": "scheduled",
        "scheduled_for": start.isoformat(),
        "schedule_interval_minutes": payload.schedule_interval_minutes,
    })
    batch.settings_snapshot = settings
    source_feed = db.scalar(select(SourceFeed).where(SourceFeed.latest_batch_id == batch.id))
    if source_feed:
        feed_settings = dict(source_feed.settings_snapshot)
        feed_settings.update({
            "visibility": "scheduled",
            "scheduled_for": start.isoformat(),
            "schedule_interval_minutes": payload.schedule_interval_minutes,
        })
        source_feed.settings_snapshot = feed_settings
    skipped_uploaded = sum(
        1 for item in batch.items if item.publish_job and item.publish_job.youtube_video_id is not None
    )
    skipped_active = sum(
        1 for item in batch.items
        if item.publish_job and item.publish_job.youtube_video_id is None and item.publish_job.state == "uploading"
    )
    db.add(AutomationEvent(
        aggregate_type="url_batch",
        aggregate_id=batch.id,
        event_type="url_batch.remaining_publications_scheduled",
        from_state=batch.state,
        to_state=batch.state,
        details={
            "scheduled_count": len(pending),
            "already_uploaded_count": skipped_uploaded,
            "active_upload_count": skipped_active,
            "first_publication_at": start.isoformat(),
            "interval_minutes": payload.schedule_interval_minutes,
            "source_feed_updated": bool(source_feed),
        },
    ))
    db.commit()
    return get_batch(db, batch.id)


def list_events(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    events = db.scalars(select(AutomationEvent).order_by(AutomationEvent.occurred_at.desc()).limit(limit)).all()
    return [{
        "id": event.id,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "batch_item_id": event.batch_item_id,
        "event_type": event.event_type,
        "from_state": event.from_state,
        "to_state": event.to_state,
        "details": event.details,
        "occurred_at": event.occurred_at,
    } for event in events]


def overview(db: Session) -> dict[str, Any]:
    channel_count = db.scalar(select(func.count()).select_from(DestinationChannel).where(
        DestinationChannel.archived_at.is_(None)
    )) or 0
    batch_count = db.scalar(select(func.count()).select_from(UrlBatch)) or 0
    item_states = dict(db.execute(select(BatchItem.state, func.count()).group_by(BatchItem.state)).all())
    recent_batches = list_batches(db, 8)
    next_upload = None
    for batch in reversed(recent_batches):
        channel = batch["destination_channel"]
        for item in batch["items"]:
            publish = item.get("publish_job") or {}
            if not item.get("output") or publish.get("state") not in {
                "blocked_not_connected", "waiting_for_upload", "uploading", "youtube_processing",
                "upload_failed", "processing_failed", "post_publish_failed",
            }:
                continue
            channel_record = db.get(DestinationChannel, channel["id"])
            if channel_record and channel_record.next_publish_at is None:
                channel_record.next_publish_at = utcnow()
                db.commit()
                channel = channel_dict(channel_record)
            next_upload = {
                "batch_id": batch["id"],
                "batch_name": batch["name"],
                "item_id": item["id"],
                "publish_job_id": publish["id"],
                "title": item.get("source_title") or item.get("source_video_id") or "Untitled video",
                "description": item.get("source_description") or "Source description will be preserved for upload.",
                "thumbnail_url": item.get("thumbnail_url"),
                "video_url": item["output"]["url"],
                "output_name": item["output"]["name"],
                "scheduled_for": publish.get("scheduled_for") or channel.get("next_publish_at"),
                "interval_minutes": (
                    batch["settings_snapshot"].get("schedule_interval_minutes", 30)
                    if publish.get("visibility") == "scheduled"
                    else channel.get("upload_interval_minutes", 15)
                ),
                "visibility": publish.get("visibility"),
                "channel": channel,
                "state": publish.get("state"),
            }
            break
        if next_upload:
            break
    oauth_config = oauth_configuration()
    return {
        "mode": "single_administrator",
        "oauth_configured": oauth_config["configured"],
        "oauth_redirect_uri": oauth_config["redirect_uri"],
        "publishing_enabled": os.getenv("YOUTUBE_PUBLISHING_ENABLED", "true").lower() not in {"0", "false", "no"},
        "automation_worker_enabled": True,
        "counts": {"channels": channel_count, "batches": batch_count, "items_by_state": item_states},
        "channels": list_channels(db),
        "recent_batches": recent_batches,
        "recent_events": list_events(db, 12),
        "next_upload": next_upload,
    }
