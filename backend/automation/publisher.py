from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from google.auth.transport.requests import AuthorizedSession
from sqlalchemy import case, func, select
from sqlalchemy.orm import joinedload, selectinload

from .database import SessionLocal
from .models import (
    AutomationEvent,
    BatchItem,
    DestinationChannel,
    GoogleConnection,
    MediaAsset,
    PublishJob,
    UploadSession,
    UrlBatch,
    utcnow,
)
from .oauth import OAuthFlowError, decrypt_oauth_value, encrypt_oauth_value, youtube_authorized_session


UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
THUMBNAILS_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
ACTIVE_STATES = {"uploading", "youtube_processing"}
RETRYABLE_STATES = {"upload_failed", "processing_failed", "post_publish_failed"}
CHUNK_SIZE = 8 * 1024 * 1024
MAX_PUBLISH_ATTEMPTS = 5
PROCESSING_POLL_INTERVAL = timedelta(seconds=30)


class PublishError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class YouTubePublisher:
    """Durable single-host YouTube resumable-upload and processing worker."""

    def __init__(self, input_dir: Path, output_dir: Path) -> None:
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.owner = f"publisher-{os.getpid()}-{secrets.token_hex(4)}"
        self.stop_event = asyncio.Event()

    async def run(self) -> None:
        self._recover_interrupted_jobs()
        while not self.stop_event.is_set():
            job_id = self._claim_next_job()
            if job_id:
                await asyncio.to_thread(self._process_job, job_id)
                continue
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self.stop_event.set()

    def _recover_interrupted_jobs(self) -> None:
        with SessionLocal.begin() as db:
            jobs = db.scalars(select(PublishJob).where(PublishJob.state.in_(ACTIVE_STATES))).all()
            for job in jobs:
                job.lease_owner = None
                job.lease_expires_at = None
                db.add(AutomationEvent(
                    aggregate_type="publish_job",
                    aggregate_id=job.id,
                    batch_item_id=job.batch_item_id,
                    event_type="publish_job.recovered",
                    from_state=job.state,
                    to_state=job.state,
                    details={"reason": "backend_restart", "acknowledged_bytes": job.upload_session.acknowledged_bytes},
                ))

    def _claim_next_job(self) -> str | None:
        now = utcnow()
        with SessionLocal.begin() as db:
            jobs = db.scalars(
                select(PublishJob)
                .where(PublishJob.state.in_(("youtube_processing", "uploading", "waiting_for_upload")))
                .order_by(
                    case(
                        (PublishJob.state == "uploading", 0),
                        (PublishJob.state == "waiting_for_upload", 1),
                        else_=2,
                    ),
                    PublishJob.created_at,
                )
                .with_for_update(skip_locked=True)
            ).unique().all()
            job = next((candidate for candidate in jobs if self._eligible(candidate, now)), None)
            if not job:
                return None
            previous = job.state
            job.state = "youtube_processing" if job.youtube_video_id else "uploading"
            if not job.youtube_video_id:
                job.attempts += 1
            channel = job.batch_item.batch.destination_channel
            if previous == "waiting_for_upload":
                if job.visibility == "scheduled":
                    self._reserve_publication_slot(db, job, now)
                else:
                    channel.next_publish_at = now + timedelta(minutes=channel.upload_interval_minutes)
            job.lease_owner = self.owner
            job.lease_expires_at = now + timedelta(minutes=30)
            job.error_code = None
            job.error_message = None
            job.batch_item.state = "youtube_processing" if job.youtube_video_id else "uploading"
            db.add(AutomationEvent(
                aggregate_type="publish_job",
                aggregate_id=job.id,
                batch_item_id=job.batch_item_id,
                event_type="publish_job.claimed",
                from_state=previous,
                to_state=job.state,
                details={
                    "attempt": job.attempts,
                    "worker": self.owner,
                    "next_publish_at": (
                        channel.next_publish_at.isoformat()
                        if previous == "waiting_for_upload" and channel.next_publish_at
                        else None
                    ),
                },
            ))
            return job.id

    @staticmethod
    def _eligible(job: PublishJob, now: datetime) -> bool:
        channel = job.batch_item.batch.destination_channel
        if channel.archived_at or channel.oauth_status != "connected" or channel.operational_state != "active":
            return False
        if job.youtube_video_id:
            next_processing_check = _aware(job.lease_expires_at)
            return next_processing_check is None or next_processing_check <= now
        if job.state in ACTIVE_STATES:
            return True
        if job.visibility == "scheduled":
            return True
        due = _aware(channel.next_publish_at)
        return due is None or due <= now

    @staticmethod
    def _reserve_publication_slot(db, job: PublishJob, now: datetime) -> None:
        interval_minutes = max(5, int(job.batch_item.batch.settings_snapshot.get("schedule_interval_minutes", 30)))
        interval = timedelta(minutes=interval_minutes)
        earliest = now + interval
        planned = _aware(job.scheduled_for) or earliest
        committed_latest = db.scalar(select(func.max(PublishJob.scheduled_for)).where(
            PublishJob.destination_channel_id == job.destination_channel_id,
            PublishJob.visibility == "scheduled",
            PublishJob.youtube_video_id.is_not(None),
            PublishJob.id != job.id,
        ))
        reserved = max(planned, earliest)
        if committed_latest is not None:
            reserved = max(reserved, _aware(committed_latest) + interval)
        if _aware(job.scheduled_for) != reserved:
            previous = _aware(job.scheduled_for)
            job.scheduled_for = reserved
            db.add(AutomationEvent(
                aggregate_type="publish_job",
                aggregate_id=job.id,
                batch_item_id=job.batch_item_id,
                event_type="publish_job.schedule_adjusted",
                from_state=job.state,
                to_state=job.state,
                details={
                    "previous_scheduled_for": previous.isoformat() if previous else None,
                    "scheduled_for": reserved.isoformat(),
                    "interval_minutes": interval_minutes,
                },
            ))

    def _process_job(self, job_id: str) -> None:
        try:
            spec = self._spec(job_id)
            session = self._authorized_session(spec)
            self._verify_destination(session, spec["youtube_channel_id"])
            if spec["youtube_video_id"]:
                self._poll_processing(session, spec)
                return
            self._upload(session, spec)
        except PublishError as error:
            self._fail(job_id, error.code, str(error), error.retryable)
        except requests.RequestException:
            self._fail(job_id, "youtube_unavailable", "YouTube could not be reached; the resumable upload can be retried", True)
        except OAuthFlowError as error:
            self._fail(job_id, error.code, str(error), False)
        except Exception as error:
            self._fail(job_id, "publish_failed", str(error), False)

    def _spec(self, job_id: str) -> dict[str, Any]:
        with SessionLocal() as db:
            job = db.scalar(
                select(PublishJob)
                .options(
                    joinedload(PublishJob.batch_item).joinedload(BatchItem.batch).joinedload(UrlBatch.destination_channel),
                    joinedload(PublishJob.upload_session),
                    joinedload(PublishJob.batch_item).selectinload(BatchItem.media_assets),
                )
                .where(PublishJob.id == job_id)
            )
            if not job:
                raise PublishError("job_missing", "The durable publish job no longer exists", retryable=False)
            item = job.batch_item
            export = next((asset for asset in item.media_assets if asset.kind == "export" and asset.state == "ready"), None)
            if not export or not export.storage_path:
                raise PublishError("export_missing", "The altered export is unavailable", retryable=False)
            path = (self.output_dir / export.storage_path).resolve()
            if self.output_dir.resolve() not in path.parents or not path.is_file() or path.stat().st_size == 0:
                raise PublishError("export_missing", "The altered export file is unavailable", retryable=False)
            metadata = export.metadata_snapshot or _read_metadata(path) or {}
            settings = dict(item.batch.settings_snapshot)
            return {
                "job_id": job.id,
                "item_id": item.id,
                "batch_id": item.batch_id,
                "connection_id": item.batch.destination_channel.google_connection.id,
                "youtube_channel_id": item.batch.destination_channel.youtube_channel_id,
                "youtube_video_id": job.youtube_video_id,
                "visibility": job.visibility,
                "scheduled_for": job.scheduled_for,
                "path": path,
                "size": path.stat().st_size,
                "title": _clean_title(metadata.get("title") or item.source_title or item.source_video_id or path.stem),
                "description": _clean_description(metadata.get("description")),
                "tags": _clean_tags(metadata.get("tags")),
                "thumbnail_url": item.thumbnail_url,
                "settings": settings,
                "session_url": decrypt_oauth_value(job.upload_session.session_url_ciphertext),
                "acknowledged_bytes": job.upload_session.acknowledged_bytes,
            }

    @staticmethod
    def _authorized_session(spec: dict[str, Any]) -> AuthorizedSession:
        with SessionLocal() as db:
            connection = db.get(GoogleConnection, spec["connection_id"])
            if not connection or connection.status != "connected":
                raise PublishError("not_connected", "The destination Google connection is unavailable", retryable=False)
            try:
                session = youtube_authorized_session(db, connection)
            except OAuthFlowError:
                connection.status = "reauthorization_required"
                connection.destination_channel.oauth_status = "reauthorization_required"
                db.commit()
                raise
            db.commit()
            return session

    @staticmethod
    def _verify_destination(session: AuthorizedSession, expected_channel_id: str) -> None:
        response = session.get(CHANNELS_URL, params={"part": "id", "mine": "true"}, timeout=30)
        if response.status_code != 200:
            raise PublishError("identity_check_failed", _response_error(response, "YouTube channel verification failed"))
        items = response.json().get("items") or []
        actual = str(items[0].get("id")) if items else ""
        if actual != expected_channel_id:
            raise PublishError(
                "channel_mismatch",
                "Google authorization no longer resolves to this destination channel",
                retryable=False,
            )

    def _upload(self, session: AuthorizedSession, spec: dict[str, Any]) -> None:
        session_url = spec["session_url"]
        offset = int(spec["acknowledged_bytes"] or 0)
        if session_url:
            status, offset, video_id = self._probe_session(session, session_url, spec["size"])
            if status == "expired":
                self._clear_session(spec["job_id"])
                session_url = None
                offset = 0
            elif video_id:
                self._record_uploaded(spec["job_id"], video_id)
                spec["youtube_video_id"] = video_id
                self._poll_processing(session, spec)
                return
            else:
                self._record_acknowledged(spec["job_id"], offset, spec["size"])

        if not session_url:
            session_url = self._start_session(session, spec)
            self._persist_session(spec["job_id"], session_url)

        with spec["path"].open("rb") as source:
            source.seek(offset)
            while offset < spec["size"]:
                chunk = source.read(min(CHUNK_SIZE, spec["size"] - offset))
                if not chunk:
                    raise PublishError("export_read_failed", "The altered export ended before the expected size", retryable=False)
                end = offset + len(chunk) - 1
                response = session.put(
                    session_url,
                    data=chunk,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{spec['size']}",
                    },
                    timeout=180,
                )
                if response.status_code == 308:
                    offset = _acknowledged_offset(response.headers.get("Range"), default=end + 1)
                    self._record_acknowledged(spec["job_id"], offset, spec["size"])
                    continue
                if response.status_code in {200, 201}:
                    video_id = str(response.json().get("id") or "")
                    if not video_id:
                        raise PublishError("youtube_id_missing", "YouTube accepted the upload without returning a video ID")
                    self._record_uploaded(spec["job_id"], video_id)
                    spec["youtube_video_id"] = video_id
                    self._poll_processing(session, spec)
                    return
                if response.status_code == 404:
                    self._clear_session(spec["job_id"])
                    raise PublishError("upload_session_expired", "The YouTube upload session expired and will restart")
                raise PublishError("upload_chunk_failed", _response_error(response, "YouTube rejected an upload chunk"))

    @staticmethod
    def _start_session(session: AuthorizedSession, spec: dict[str, Any]) -> str:
        privacy = "private" if spec["visibility"] == "scheduled" else spec["visibility"]
        status: dict[str, Any] = {"privacyStatus": privacy}
        if spec["visibility"] == "scheduled" and spec["scheduled_for"]:
            status["publishAt"] = _aware(spec["scheduled_for"]).isoformat().replace("+00:00", "Z")
        body = {
            "snippet": {
                "title": spec["title"],
                "description": spec["description"],
                "tags": spec["tags"],
                "categoryId": "22",
            },
            "status": status,
        }
        response = session.post(
            UPLOAD_URL,
            params={
                "uploadType": "resumable",
                "part": "snippet,status",
                "notifySubscribers": str(bool(spec["settings"].get("notify_subscribers", False))).lower(),
            },
            json=body,
            headers={"X-Upload-Content-Length": str(spec["size"]), "X-Upload-Content-Type": "video/mp4"},
            timeout=60,
        )
        session_url = response.headers.get("Location")
        if response.status_code not in {200, 201} or not session_url:
            raise PublishError("upload_session_failed", _response_error(response, "YouTube could not start an upload session"))
        return session_url

    @staticmethod
    def _probe_session(
        session: AuthorizedSession,
        session_url: str,
        total_size: int,
    ) -> tuple[str, int, str | None]:
        response = session.put(
            session_url,
            data=b"",
            headers={"Content-Length": "0", "Content-Range": f"bytes */{total_size}"},
            timeout=60,
        )
        if response.status_code == 308:
            return "active", _acknowledged_offset(response.headers.get("Range")), None
        if response.status_code in {200, 201}:
            video_id = str(response.json().get("id") or "")
            return "complete", total_size, video_id or None
        if response.status_code == 404:
            return "expired", 0, None
        raise PublishError("upload_probe_failed", _response_error(response, "YouTube could not resume the upload"))

    def _poll_processing(self, session: AuthorizedSession, spec: dict[str, Any]) -> None:
        video_id = str(spec["youtube_video_id"])
        response = session.get(
            VIDEOS_URL,
            params={"part": "status,processingDetails", "id": video_id},
            timeout=30,
        )
        if response.status_code != 200:
            raise PublishError("processing_check_failed", _response_error(response, "YouTube processing status is unavailable"))
        items = response.json().get("items") or []
        if not items:
            raise PublishError("uploaded_video_missing", "YouTube no longer returns the uploaded video", retryable=False)
        video = items[0]
        processing = str((video.get("processingDetails") or {}).get("processingStatus") or "")
        upload_status = str((video.get("status") or {}).get("uploadStatus") or "")
        if processing == "failed" or upload_status in {"failed", "rejected", "deleted"}:
            raise PublishError("processing_failed", "YouTube could not process the uploaded video", retryable=False)
        if processing != "succeeded" and upload_status != "processed":
            self._release_processing(spec["job_id"])
            return
        try:
            self._apply_thumbnail(session, spec, video_id)
            self._apply_playlists(session, spec, video_id)
        except PublishError as error:
            self._fail(spec["job_id"], "post_publish_failed", str(error), True)
            return
        self._complete(spec["job_id"], video_id)

    @staticmethod
    def _apply_thumbnail(session: AuthorizedSession, spec: dict[str, Any], video_id: str) -> None:
        settings = spec["settings"]
        if settings.get("output_profile") == "youtube-shorts":
            return
        if settings.get("thumbnail_mode") != "source" or not spec.get("thumbnail_url"):
            return
        response = requests.get(spec["thumbnail_url"], timeout=30)
        if response.status_code != 200 or len(response.content) > 2 * 1024 * 1024:
            raise PublishError("thumbnail_download_failed", "The source thumbnail could not be prepared")
        content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
        upload = session.post(
            THUMBNAILS_URL,
            params={"videoId": video_id, "uploadType": "media"},
            data=response.content,
            headers={"Content-Type": content_type},
            timeout=60,
        )
        if upload.status_code not in {200, 201}:
            raise PublishError("thumbnail_upload_failed", _response_error(upload, "YouTube rejected the source thumbnail"))

    @staticmethod
    def _apply_playlists(session: AuthorizedSession, spec: dict[str, Any], video_id: str) -> None:
        for playlist_id in spec["settings"].get("playlist_ids") or []:
            current = session.get(
                PLAYLIST_ITEMS_URL,
                params={"part": "id", "playlistId": playlist_id, "videoId": video_id, "maxResults": 1},
                timeout=30,
            )
            if current.status_code != 200:
                raise PublishError("playlist_check_failed", _response_error(current, "A target playlist could not be checked"))
            if current.json().get("items"):
                continue
            inserted = session.post(
                PLAYLIST_ITEMS_URL,
                params={"part": "snippet"},
                json={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
                timeout=30,
            )
            if inserted.status_code not in {200, 201}:
                raise PublishError("playlist_insert_failed", _response_error(inserted, "The uploaded video could not be added to a playlist"))

    @staticmethod
    def _persist_session(job_id: str, session_url: str) -> None:
        with SessionLocal.begin() as db:
            job = db.get(PublishJob, job_id)
            if not job:
                return
            upload = job.upload_session
            upload.state = "uploading"
            upload.session_url_ciphertext = encrypt_oauth_value(session_url)
            upload.acknowledged_bytes = 0
            upload.expires_at = utcnow() + timedelta(days=6)
            upload.last_error = None
            db.add(AutomationEvent(
                aggregate_type="publish_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
                event_type="publish_job.upload_session_started", from_state="uploading", to_state="uploading",
                details={"total_bytes": _export_size(job.batch_item)},
            ))

    @staticmethod
    def _clear_session(job_id: str) -> None:
        with SessionLocal.begin() as db:
            job = db.get(PublishJob, job_id)
            if job:
                job.upload_session.state = "expired"
                job.upload_session.session_url_ciphertext = None
                job.upload_session.acknowledged_bytes = 0
                job.upload_session.expires_at = None

    @staticmethod
    def _record_acknowledged(job_id: str, offset: int, total: int) -> None:
        with SessionLocal.begin() as db:
            job = db.get(PublishJob, job_id)
            if not job:
                return
            job.upload_session.acknowledged_bytes = max(job.upload_session.acknowledged_bytes, offset)
            job.progress = min(99, int(offset * 100 / total)) if total else 0
            job.lease_expires_at = utcnow() + timedelta(minutes=30)

    @staticmethod
    def _record_uploaded(job_id: str, video_id: str) -> None:
        with SessionLocal.begin() as db:
            job = db.get(PublishJob, job_id)
            if not job:
                return
            previous = job.state
            job.youtube_video_id = video_id
            job.state = "youtube_processing"
            job.progress = 99
            job.batch_item.state = "youtube_processing"
            job.upload_session.state = "uploaded"
            job.upload_session.session_url_ciphertext = None
            job.upload_session.acknowledged_bytes = _export_size(job.batch_item)
            job.upload_session.expires_at = None
            db.add(AutomationEvent(
                aggregate_type="publish_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
                event_type="publish_job.upload_completed", from_state=previous, to_state="youtube_processing",
                details={"youtube_video_id": video_id},
            ))

    @staticmethod
    def _release_processing(job_id: str) -> None:
        with SessionLocal.begin() as db:
            job = db.get(PublishJob, job_id)
            if job:
                job.state = "youtube_processing"
                job.batch_item.state = "youtube_processing"
                job.lease_owner = None
                job.lease_expires_at = utcnow() + PROCESSING_POLL_INTERVAL

    def _complete(self, job_id: str, video_id: str) -> None:
        source_path: Path | None = None
        with SessionLocal.begin() as db:
            job = db.get(PublishJob, job_id)
            if not job:
                return
            previous = job.state
            item = job.batch_item
            settings = item.batch.settings_snapshot
            if settings.get("delete_source_after_success"):
                source = next((asset for asset in item.media_assets if asset.kind == "source"), None)
                if source and source.storage_path:
                    source_path = (self.input_dir / source.storage_path).resolve()
                    if self.input_dir.resolve() not in source_path.parents:
                        source_path = None
                    else:
                        source.state = "deleted"
                        source.deleted_at = utcnow()
            completed_state = "scheduled" if job.visibility == "scheduled" else "published"
            job.state = completed_state
            job.progress = 100
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = None
            job.error_message = None
            item.state = completed_state
            channel = item.batch.destination_channel
            channel.uploads_today += 1
            db.add(AutomationEvent(
                aggregate_type="publish_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
                event_type=f"publish_job.{completed_state}", from_state=previous, to_state=completed_state,
                details={
                    "youtube_video_id": video_id,
                    "visibility": job.visibility,
                    "scheduled_for": job.scheduled_for.isoformat() if job.scheduled_for else None,
                },
            ))
            _update_batch_state(db, item.batch_id)
        if source_path:
            source_path.unlink(missing_ok=True)
            _metadata_path(source_path).unlink(missing_ok=True)

    @staticmethod
    def _fail(job_id: str, code: str, message: str, retryable: bool) -> None:
        with SessionLocal.begin() as db:
            job = db.get(PublishJob, job_id)
            if not job:
                return
            previous = job.state
            if job.youtube_video_id:
                job.attempts += 1
            if code in {"channel_mismatch", "reauthorization_required"}:
                channel = db.get(DestinationChannel, job.destination_channel_id)
                connection = channel.google_connection if channel else None
                if channel:
                    channel.oauth_status = code
                if connection:
                    connection.status = code
            if code in {"channel_mismatch", "not_connected", "reauthorization_required"}:
                state = "blocked_not_connected"
            elif retryable and job.attempts < MAX_PUBLISH_ATTEMPTS:
                state = "youtube_processing" if job.youtube_video_id else "waiting_for_upload"
            else:
                state = "processing_failed" if job.youtube_video_id else "upload_failed"
            if code == "post_publish_failed":
                state = "post_publish_failed"
            job.state = state
            if state == "blocked_not_connected":
                job.batch_item.state = "rendered"
            elif state in RETRYABLE_STATES:
                job.batch_item.state = "failed"
            elif state == "waiting_for_upload":
                job.batch_item.state = "rendered"
            job.lease_owner = None
            job.lease_expires_at = (
                utcnow() + PROCESSING_POLL_INTERVAL
                if state == "youtube_processing"
                else None
            )
            job.error_code = code
            job.error_message = message[-2000:]
            job.upload_session.last_error = message[-2000:]
            db.add(AutomationEvent(
                aggregate_type="publish_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
                event_type="publish_job.failed" if state in RETRYABLE_STATES else "publish_job.retry_scheduled",
                from_state=previous, to_state=state,
                details={"error_code": code, "retryable": retryable},
            ))
            _update_batch_state(db, job.batch_item.batch_id)


def retry_publish_job(db, job_id: str) -> dict[str, Any]:
    job = db.get(PublishJob, job_id)
    if not job:
        raise ValueError("Publish job not found")
    if job.state not in RETRYABLE_STATES:
        raise ValueError("Only failed publish jobs can be retried")
    if job.attempts >= MAX_PUBLISH_ATTEMPTS:
        raise ValueError(f"This video reached the maximum of {MAX_PUBLISH_ATTEMPTS} publish attempts")
    previous = job.state
    job.state = "youtube_processing" if job.youtube_video_id else "waiting_for_upload"
    job.progress = 99 if job.youtube_video_id else int(job.upload_session.acknowledged_bytes * 100 / max(1, _export_size(job.batch_item)))
    job.error_code = None
    job.error_message = None
    job.batch_item.state = "youtube_processing" if job.youtube_video_id else "rendered"
    job.batch_item.batch.state = "publishing"
    db.add(AutomationEvent(
        aggregate_type="publish_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
        event_type="publish_job.retry_requested", from_state=previous, to_state=job.state,
        details={"next_attempt": job.attempts + 1},
    ))
    db.commit()
    return {"id": job.id, "state": job.state}


def make_channel_videos_public(db, channel_id: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, channel_id)
    if not channel or channel.archived_at:
        raise ValueError("Destination channel not found")
    connection = channel.google_connection
    if channel.oauth_status != "connected" or not connection or connection.status != "connected":
        raise ValueError("Reconnect this destination before changing YouTube visibility")
    session = youtube_authorized_session(db, connection)
    YouTubePublisher._verify_destination(session, channel.youtube_channel_id)
    jobs = db.scalars(
        select(PublishJob)
        .options(joinedload(PublishJob.batch_item).joinedload(BatchItem.batch))
        .where(
            PublishJob.destination_channel_id == channel.id,
            PublishJob.visibility.in_(("private", "unlisted", "public")),
        )
        .order_by(PublishJob.created_at)
    ).unique().all()
    updated = 0
    already_public = 0
    pending_updated = 0
    failures: list[dict[str, str]] = []
    touched_batches: set[str] = set()
    writable_status_fields = {
        "embeddable",
        "license",
        "publicStatsViewable",
        "selfDeclaredMadeForKids",
        "containsSyntheticMedia",
    }
    for job in jobs:
        touched_batches.add(job.batch_item.batch_id)
        if not job.youtube_video_id:
            previous = job.visibility
            job.visibility = "public"
            if previous != "public":
                pending_updated += 1
                db.add(AutomationEvent(
                    aggregate_type="publish_job",
                    aggregate_id=job.id,
                    batch_item_id=job.batch_item_id,
                    event_type="publish_job.visibility_changed",
                    from_state=previous,
                    to_state="public",
                    details={"youtube_video_id": None, "reason": "public_only_policy"},
                ))
            continue
        current = session.get(VIDEOS_URL, params={"part": "status", "id": job.youtube_video_id}, timeout=30)
        items = (current.json().get("items") or []) if current.status_code == 200 else []
        if not items:
            failures.append({"youtube_video_id": job.youtube_video_id, "error": _response_error(current, "Video status unavailable")})
            continue
        existing_status = items[0].get("status") or {}
        if existing_status.get("privacyStatus") == "public":
            previous = job.visibility
            job.visibility = "public"
            already_public += 1
            if previous != "public":
                db.add(AutomationEvent(
                    aggregate_type="publish_job",
                    aggregate_id=job.id,
                    batch_item_id=job.batch_item_id,
                    event_type="publish_job.visibility_changed",
                    from_state=previous,
                    to_state="public",
                    details={"youtube_video_id": job.youtube_video_id, "reason": "verified_public"},
                ))
            continue
        status = {key: existing_status[key] for key in writable_status_fields if key in existing_status}
        status["privacyStatus"] = "public"
        response = session.put(
            VIDEOS_URL,
            params={"part": "status"},
            json={"id": job.youtube_video_id, "status": status},
            timeout=30,
        )
        if response.status_code != 200:
            failures.append({"youtube_video_id": job.youtube_video_id, "error": _response_error(response, "YouTube visibility update failed")})
            continue
        previous = job.visibility
        job.visibility = "public"
        updated += 1
        db.add(AutomationEvent(
            aggregate_type="publish_job",
            aggregate_id=job.id,
            batch_item_id=job.batch_item_id,
            event_type="publish_job.visibility_changed",
            from_state=previous,
            to_state="public",
            details={"youtube_video_id": job.youtube_video_id, "reason": "public_only_policy"},
        ))
        db.commit()
    for batch_id in touched_batches:
        batch = db.get(UrlBatch, batch_id)
        if batch and batch.settings_snapshot.get("visibility") != "scheduled":
            settings = dict(batch.settings_snapshot)
            settings.update({"visibility": "public", "scheduled_for": None})
            batch.settings_snapshot = settings
    scheduled_jobs = db.scalars(
        select(PublishJob)
        .options(joinedload(PublishJob.batch_item).joinedload(BatchItem.batch))
        .where(
            PublishJob.destination_channel_id == channel.id,
            PublishJob.visibility == "scheduled",
            PublishJob.youtube_video_id.is_not(None),
        )
    ).unique().all()
    scheduled_by_video_id = {job.youtube_video_id: job for job in scheduled_jobs}
    scheduled_public_at_release = 0
    scheduled_already_public = 0
    for offset in range(0, len(scheduled_jobs), 50):
        video_ids = [job.youtube_video_id for job in scheduled_jobs[offset:offset + 50]]
        response = session.get(
            VIDEOS_URL,
            params={"part": "status", "id": ",".join(video_ids)},
            timeout=30,
        )
        items = (response.json().get("items") or []) if response.status_code == 200 else []
        returned_ids: set[str] = set()
        for item in items:
            video_id = item.get("id")
            job = scheduled_by_video_id.get(video_id)
            if not job:
                continue
            returned_ids.add(video_id)
            status = item.get("status") or {}
            if status.get("privacyStatus") == "private" and status.get("publishAt"):
                scheduled_public_at_release += 1
                continue
            if status.get("privacyStatus") == "public":
                previous_state = job.state
                job.visibility = "public"
                job.state = "published"
                job.progress = 100
                job.batch_item.state = "published"
                touched_batches.add(job.batch_item.batch_id)
                scheduled_already_public += 1
                db.add(AutomationEvent(
                    aggregate_type="publish_job",
                    aggregate_id=job.id,
                    batch_item_id=job.batch_item_id,
                    event_type="publish_job.scheduled_release_observed",
                    from_state=previous_state,
                    to_state="published",
                    details={
                        "youtube_video_id": job.youtube_video_id,
                        "scheduled_for": job.scheduled_for.isoformat() if job.scheduled_for else None,
                    },
                ))
                continue
            failures.append({
                "youtube_video_id": job.youtube_video_id,
                "error": "Scheduled video no longer has a public release time",
            })
        for video_id in set(video_ids) - returned_ids:
            failures.append({
                "youtube_video_id": video_id,
                "error": _response_error(response, "Scheduled video status unavailable"),
            })
    for batch_id in touched_batches:
        _update_batch_state(db, batch_id)
    db.add(AutomationEvent(
        aggregate_type="destination_channel",
        aggregate_id=channel.id,
        event_type="destination_channel.public_visibility_enforced",
        from_state=channel.operational_state,
        to_state=channel.operational_state,
        details={
            "updated_on_youtube": updated,
            "already_public": already_public,
            "pending_jobs_updated": pending_updated,
            "scheduled_public_at_release": scheduled_public_at_release,
            "scheduled_already_public": scheduled_already_public,
            "failure_count": len(failures),
        },
    ))
    db.commit()
    return {
        "updated_on_youtube": updated,
        "already_public": already_public,
        "pending_jobs_updated": pending_updated,
        "scheduled_public_at_release": scheduled_public_at_release,
        "scheduled_already_public": scheduled_already_public,
        "failures": failures,
    }


def _update_batch_state(db, batch_id: str) -> None:
    batch = db.get(UrlBatch, batch_id)
    if not batch:
        return
    items = [item for item in batch.items if item.validation_status == "accepted"]
    states = [item.publish_job.state for item in items if item.publish_job]
    previous = batch.state
    if states and all(state in {"published", "scheduled"} for state in states):
        batch.state = "scheduled" if any(state == "scheduled" for state in states) else "published"
    elif any(state in RETRYABLE_STATES for state in states):
        batch.state = "publish_failed"
    elif any(state in ACTIVE_STATES for state in states):
        batch.state = "publishing"
    elif states:
        batch.state = "rendered_awaiting_upload"
    if batch.state != previous:
        db.add(AutomationEvent(
            aggregate_type="url_batch", aggregate_id=batch.id,
            event_type="url_batch.state_changed", from_state=previous, to_state=batch.state,
            details={},
        ))


def _export_size(item: BatchItem) -> int:
    export = next((asset for asset in item.media_assets if asset.kind == "export" and asset.state == "ready"), None)
    return int(export.size_bytes or 0) if export else 0


def _acknowledged_offset(value: str | None, default: int = 0) -> int:
    match = re.fullmatch(r"bytes=\d+-(\d+)", value or "")
    return int(match.group(1)) + 1 if match else default


def _response_error(response: requests.Response, fallback: str) -> str:
    try:
        payload = response.json()
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
    except (ValueError, AttributeError):
        message = None
    return f"{fallback} (HTTP {response.status_code}): {str(message)[:1000]}" if message else f"{fallback} (HTTP {response.status_code})"


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "Untitled video").split())
    return title[:100] or "Untitled video"


def _clean_description(value: Any) -> str:
    return str(value or "")[:5000]


def _clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    length = 0
    for raw in value:
        tag = " ".join(str(raw).split())[:100]
        if not tag or length + len(tag) > 450:
            continue
        result.append(tag)
        length += len(tag)
    return result


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _metadata_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.metadata.json")


def _read_metadata(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_metadata_path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
