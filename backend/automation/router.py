from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .database import get_db
from .oauth import (
    OAuthFlowError,
    adopt_oauth_candidate,
    complete_google_oauth,
    discard_oauth_candidate,
    disconnect_google,
    frontend_callback_url,
    start_google_oauth,
    verify_google_connection,
)
from .schemas import AutomationEditRequest, BatchCreate, BatchScheduleRequest, ChannelCreate, SourceFeedCreate, UrlPreviewRequest
from .service import (
    create_batch,
    create_channel,
    archive_channel,
    get_batch,
    create_source_feed,
    list_batches,
    list_channels,
    list_events,
    list_source_feeds,
    overview,
    preview_urls,
    save_automation_edit,
    schedule_remaining_batch_publications,
    set_batch_processing,
    sync_source_feed,
)
from .worker import retry_render_job
from .publisher import make_channel_videos_public, retry_publish_job


router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/overview")
def get_overview(db: Session = Depends(get_db)) -> dict:
    return overview(db)


@router.get("/channels")
def get_channels(db: Session = Depends(get_db)) -> list[dict]:
    return list_channels(db)


@router.post("/channels", status_code=201)
def register_channel(payload: ChannelCreate, db: Session = Depends(get_db)) -> dict:
    return create_channel(db, payload)


@router.delete("/channels/{channel_id}")
def remove_channel(channel_id: str, db: Session = Depends(get_db)) -> dict:
    return archive_channel(db, channel_id)


@router.post("/channels/{channel_id}/oauth/start")
def begin_channel_oauth(channel_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return start_google_oauth(db, channel_id)
    except OAuthFlowError as error:
        raise HTTPException(503, str(error)) from error


@router.get("/oauth/google/callback", include_in_schema=False)
def google_oauth_callback(
    state: str = "",
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        result = complete_google_oauth(db, state, code, error)
        if result.get("status") == "pending_adoption":
            return RedirectResponse(frontend_callback_url("pending_adoption"), status_code=303)
        return RedirectResponse(frontend_callback_url("success", name=result["display_name"]), status_code=303)
    except OAuthFlowError as oauth_error:
        return RedirectResponse(frontend_callback_url("error", code=oauth_error.code), status_code=303)


@router.post("/channels/{channel_id}/oauth/verify")
def verify_channel_oauth(channel_id: str, db: Session = Depends(get_db)) -> dict:
    return verify_google_connection(db, channel_id)


@router.post("/channels/{channel_id}/oauth/candidates/{candidate_id}/adopt")
def adopt_channel_oauth_candidate(
    channel_id: str,
    candidate_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return adopt_oauth_candidate(db, channel_id, candidate_id)


@router.delete("/channels/{channel_id}/oauth/candidates/{candidate_id}")
def discard_channel_oauth_candidate(
    channel_id: str,
    candidate_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return discard_oauth_candidate(db, channel_id, candidate_id)


@router.delete("/channels/{channel_id}/oauth")
def revoke_channel_oauth(channel_id: str, db: Session = Depends(get_db)) -> dict:
    return disconnect_google(db, channel_id)


@router.post("/channels/{channel_id}/visibility/public")
def enforce_public_channel_visibility(channel_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return make_channel_videos_public(db, channel_id)
    except ValueError as error:
        status = 404 if str(error) == "Destination channel not found" else 409
        raise HTTPException(status, str(error)) from error


@router.post("/url-batches/preview")
def preview_batch(payload: UrlPreviewRequest, db: Session = Depends(get_db)) -> dict:
    return preview_urls(db, payload.destination_channel_id, payload.urls)


@router.post("/url-batches", status_code=201)
def persist_batch(payload: BatchCreate, db: Session = Depends(get_db)) -> dict:
    return create_batch(db, payload)


@router.get("/source-feeds")
def get_source_feeds(db: Session = Depends(get_db)) -> list[dict]:
    return list_source_feeds(db)


@router.post("/source-feeds", status_code=201)
def register_source_feed(payload: SourceFeedCreate, db: Session = Depends(get_db)) -> dict:
    return create_source_feed(db, payload)


@router.post("/source-feeds/{feed_id}/sync")
def sync_configured_source_feed(feed_id: str, db: Session = Depends(get_db)) -> dict:
    return sync_source_feed(db, feed_id)


@router.get("/url-batches")
def get_batches(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)) -> list[dict]:
    return list_batches(db, limit)


@router.get("/url-batches/{batch_id}")
def get_batch_detail(batch_id: str, db: Session = Depends(get_db)) -> dict:
    return get_batch(db, batch_id)


@router.post("/url-batches/{batch_id}/pause-processing")
def pause_batch_processing(batch_id: str, db: Session = Depends(get_db)) -> dict:
    return set_batch_processing(db, batch_id, "paused")


@router.post("/url-batches/{batch_id}/resume-processing")
def resume_batch_processing(batch_id: str, db: Session = Depends(get_db)) -> dict:
    return set_batch_processing(db, batch_id, "running")


@router.post("/url-batches/{batch_id}/schedule-remaining")
def schedule_remaining_publications(
    batch_id: str,
    payload: BatchScheduleRequest,
    db: Session = Depends(get_db),
) -> dict:
    return schedule_remaining_batch_publications(db, batch_id, payload)


@router.get("/events")
def get_events(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> list[dict]:
    return list_events(db, limit)


@router.post("/render-jobs/{job_id}/retry")
def retry_failed_render(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return retry_render_job(db, job_id)
    except ValueError as error:
        status = 404 if str(error) == "Render job not found" else 409
        raise HTTPException(status, str(error)) from error


@router.post("/publish-jobs/{job_id}/retry")
def retry_failed_publish(job_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return retry_publish_job(db, job_id)
    except ValueError as error:
        status = 404 if str(error) == "Publish job not found" else 409
        raise HTTPException(status, str(error)) from error


@router.post("/render-jobs/{job_id}/save-edit")
def save_edited_video(job_id: str, payload: AutomationEditRequest, db: Session = Depends(get_db)) -> dict:
    return save_automation_edit(db, job_id, payload)
