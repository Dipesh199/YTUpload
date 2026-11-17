from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from processor import OUTPUT_PROFILES, clean_partial, output_dimensions, probe, process_video

from .database import SessionLocal
from .models import AutomationEvent, BatchItem, MediaAsset, PipelineVersion, PublishJob, RenderJob, UrlBatch, utcnow


ACTIVE_STATES = {"acquiring", "downloading", "rendering", "validating"}
FAILED_STATES = {"download_failed", "render_failed"}


class AutomationWorker:
    """Single-host durable acquisition/render worker.

    Database transitions are committed before and after every external operation.
    Deterministic filenames and unique media-asset rows make retries safe after a
    backend or container restart.
    """

    def __init__(self, input_dir: Path, output_dir: Path, template_dir: Path) -> None:
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.template_dir = template_dir
        self.owner = f"automation-{os.getpid()}-{secrets.token_hex(4)}"
        self.stop_event = asyncio.Event()

    async def run(self) -> None:
        self._recover_interrupted_jobs()
        while not self.stop_event.is_set():
            job_id = self._claim_next_job()
            if job_id:
                await self._process_job(job_id)
                continue
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self.stop_event.set()

    def _recover_interrupted_jobs(self) -> None:
        with SessionLocal.begin() as db:
            jobs = db.scalars(select(RenderJob).where(RenderJob.state.in_(ACTIVE_STATES))).all()
            for job in jobs:
                previous = job.state
                job.state = "edit_requested" if job.error_code == "manual_edit_requested" else "waiting_for_source"
                job.lease_owner = None
                job.lease_expires_at = None
                job.batch_item.state = "queued"
                db.add(AutomationEvent(
                    aggregate_type="render_job",
                    aggregate_id=job.id,
                    batch_item_id=job.batch_item_id,
                    event_type="render_job.recovered",
                    from_state=previous,
                    to_state=job.state,
                    details={"reason": "backend_restart"},
                ))

    def _claim_next_job(self) -> str | None:
        with SessionLocal.begin() as db:
            job = db.scalar(
                select(RenderJob)
                .join(RenderJob.batch_item)
                .join(BatchItem.batch)
                .where(RenderJob.state.in_(("waiting_for_source", "retry_requested", "edit_requested")))
                .where(UrlBatch.processing_state == "running")
                .order_by(RenderJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return None
            previous = job.state
            manual_edit = previous == "edit_requested"
            job.state = "acquiring"
            job.progress = 0
            job.attempts += 1
            job.lease_owner = self.owner
            job.lease_expires_at = utcnow() + timedelta(hours=6)
            job.error_code = "manual_edit_requested" if manual_edit else None
            job.error_message = None
            job.batch_item.state = "downloading"
            job.batch_item.batch.state = "processing"
            db.add(AutomationEvent(
                aggregate_type="render_job",
                aggregate_id=job.id,
                batch_item_id=job.batch_item_id,
                event_type="render_job.claimed",
                from_state=previous,
                to_state="acquiring",
                details={"attempt": job.attempts, "worker": self.owner},
            ))
            return job.id

    def _spec(self, job_id: str) -> dict[str, Any]:
        with SessionLocal() as db:
            job = db.scalar(
                select(RenderJob)
                .options(
                    joinedload(RenderJob.batch_item).joinedload(BatchItem.batch),
                    joinedload(RenderJob.batch_item).selectinload(BatchItem.media_assets),
                )
                .where(RenderJob.id == job_id)
            )
            if not job:
                raise RuntimeError("The durable render job no longer exists")
            item = job.batch_item
            version = db.get(PipelineVersion, job.pipeline_version_id)
            settings = dict(version.settings_snapshot if version else item.batch.settings_snapshot)
            return {
                "job_id": job.id,
                "item_id": item.id,
                "batch_id": item.batch_id,
                "position": item.position,
                "url": item.normalized_url or item.raw_url,
                "video_id": item.source_video_id or "youtube",
                "settings": settings,
                "assets": {asset.kind: asset.storage_path for asset in item.media_assets if asset.storage_path},
                "force_render": job.error_code == "manual_edit_requested",
            }

    async def _process_job(self, job_id: str) -> None:
        stage = "download"
        try:
            spec = self._spec(job_id)
            source = self.input_dir / f"AUTO-SRC-{spec['item_id']}-{spec['video_id']}.mp4"
            if not await self._valid_video(source):
                await self._download(job_id, spec, source)
            await self._record_source(job_id, source)

            stage = "render"
            template_id = str(spec["settings"]["template_id"])
            destination = (
                self.output_dir / spec["assets"]["export"]
                if spec["force_render"] and spec["assets"].get("export")
                else self.output_dir / (
                    f"AUTO-{spec['batch_id'][:8]}-{spec['position']:03d}-"
                    f"{spec['video_id']}-{_safe_name(template_id)}.mp4"
                )
            )
            if spec["force_render"] or not await self._valid_render(source, destination, spec):
                await self._render(job_id, spec, source, destination)
            await self._complete(job_id, source, destination)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._fail(job_id, stage, error)

    async def _download(self, job_id: str, spec: dict[str, Any], destination: Path) -> None:
        self._transition(job_id, "downloading", "downloading", 1, "render_job.download_started")
        for partial in self.input_dir.glob(f"{destination.stem}.*.part"):
            partial.unlink(missing_ok=True)
        output_template = str(destination.with_suffix(".%(ext)s"))
        command = [
            sys.executable, "-m", "yt_dlp",
            "--newline", "--no-colors", "--no-playlist", "--windows-filenames",
            "--js-runtimes", "deno", "--progress",
            "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "3",
            "--file-access-retries", "3", "--socket-timeout", "30",
            "--retry-sleep", "http:exp=1:8", "--retry-sleep", "fragment:exp=1:8",
            "--write-info-json", "--merge-output-format", "mp4", "--remux-video", "mp4",
            "-f", (
                "bv[ext=mp4][vcodec^=avc][protocol=https]+ba[ext=m4a][protocol=https]/"
                "bv[ext=mp4][protocol=https]+ba[ext=m4a][protocol=https]/"
                "b[ext=mp4][protocol=https]/bv[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv+ba/b"
            ),
            "-o", output_template, "--", spec["url"],
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout and process.stderr
        errors: list[str] = []
        last_progress = -1

        async def consume(stream: asyncio.StreamReader) -> None:
            nonlocal last_progress
            while raw_line := await stream.readline():
                line = raw_line.decode("utf-8", errors="replace").strip()
                if "ERROR:" in line or "WARNING:" in line:
                    errors.append(line)
                    del errors[:-30]
                match = re.search(r"(\d+(?:\.\d+)?)%", line)
                if match:
                    current = min(34, max(1, int(float(match.group(1)) * .34)))
                    if current >= last_progress + 3:
                        last_progress = current
                        self._transition(job_id, "downloading", "downloading", current)

        await asyncio.gather(consume(process.stdout), consume(process.stderr), process.wait())
        if process.returncode:
            raise RuntimeError(errors[-1] if errors else f"yt-dlp exited with code {process.returncode}")
        if not await self._valid_video(destination):
            raise RuntimeError("The download completed without producing a valid MP4 video")

        info_path = destination.with_suffix(".info.json")
        metadata = _youtube_metadata(info_path, spec["url"], destination.stem)
        _write_metadata(destination, metadata)
        info_path.unlink(missing_ok=True)
        with SessionLocal.begin() as db:
            item = db.get(BatchItem, spec["item_id"])
            if item:
                item.source_title = metadata.get("title") or item.source_title

    async def _record_source(self, job_id: str, source: Path) -> None:
        digest = await asyncio.to_thread(_sha256, source)
        with SessionLocal.begin() as db:
            job = db.get(RenderJob, job_id)
            if not job:
                return
            asset = _asset(db, job.batch_item_id, "source")
            asset.state = "ready"
            asset.storage_path = source.name
            asset.sha256 = digest
            asset.size_bytes = source.stat().st_size
            asset.metadata_snapshot = _read_metadata(source)
            asset.deleted_at = None
            previous = job.state
            job.state = "rendering"
            job.progress = max(job.progress, 35)
            job.batch_item.state = "rendering"
            db.add(AutomationEvent(
                aggregate_type="render_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
                event_type="render_job.source_ready", from_state=previous, to_state="rendering",
                details={"source_asset_id": asset.id, "size_bytes": asset.size_bytes},
            ))

    async def _render(
        self,
        job_id: str,
        spec: dict[str, Any],
        source: Path,
        destination: Path,
    ) -> None:
        template = self._load_template(spec["settings"])
        working = destination.with_name(f".{destination.stem}.rendering.mp4")
        clean_partial(working)
        self._transition(job_id, "rendering", "rendering", 35, "render_job.render_started")

        last_progress = 34

        async def update(progress: float, _message: str) -> None:
            nonlocal last_progress
            current = min(94, 35 + int(progress * .59))
            if current >= last_progress + 2:
                last_progress = current
                self._transition(job_id, "rendering", "rendering", current)

        free_bytes = shutil.disk_usage(self.output_dir).free
        required = max(source.stat().st_size * 2, 256 * 1024 * 1024)
        if free_bytes < required:
            raise RuntimeError(
                f"Not enough disk space: about {required / 1024**3:.1f} GB is required, "
                f"but only {free_bytes / 1024**3:.1f} GB is free"
            )
        await process_video(source, working, template, self.template_dir, self.input_dir, update)
        if not working.exists() or working.stat().st_size == 0:
            raise RuntimeError("FFmpeg finished without creating an output video")
        self._transition(job_id, "validating", "validating", 96, "render_job.validation_started")
        if not await self._valid_render(source, working, spec):
            clean_partial(working)
            raise RuntimeError("The altered export failed duration or dimension validation")
        working.replace(destination)
        metadata = _read_metadata(source)
        if metadata:
            _write_metadata(destination, {**metadata, "derived_from": source.name})

    async def _valid_video(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            info = await probe(path)
            return info["duration"] > 0 and info["width"] > 0 and info["height"] > 0
        except Exception:
            return False

    async def _valid_render(self, source: Path, path: Path, spec: dict[str, Any]) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            template = self._load_template(spec["settings"])
            info = await probe(path)
            width, height = await output_dimensions(source, template)
            return info["duration"] > 0 and info["width"] == width and info["height"] == height
        except Exception:
            return False

    def _load_template(self, settings: dict[str, Any]) -> dict[str, Any]:
        editor_settings = settings.get("editor_settings")
        if isinstance(editor_settings, dict):
            template = json.loads(json.dumps(editor_settings))
        else:
            template_id = str(settings["template_id"])
            path = self.template_dir / f"{_safe_name(template_id)}.json"
            if not path.exists():
                raise RuntimeError(f"Remix preset '{template_id}' no longer exists")
            try:
                template = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"Remix preset '{template_id}' is invalid: {error}") from error
        template.pop("output", None)
        profile_id = str(settings.get("output_profile") or "youtube-1080p")
        if profile_id not in OUTPUT_PROFILES:
            raise RuntimeError(f"Unknown output profile '{profile_id}'")
        template["output_profile"] = dict(OUTPUT_PROFILES[profile_id])
        return template

    async def _complete(self, job_id: str, source: Path, destination: Path) -> None:
        digest = await asyncio.to_thread(_sha256, destination)
        with SessionLocal.begin() as db:
            job = db.get(RenderJob, job_id)
            if not job:
                return
            item = job.batch_item
            settings = item.batch.settings_snapshot
            export = _asset(db, item.id, "export")
            export.state = "ready"
            export.storage_path = destination.name
            export.sha256 = digest
            export.size_bytes = destination.stat().st_size
            export.metadata_snapshot = _read_metadata(destination)
            export.retained_until = utcnow() + timedelta(days=int(settings.get("export_retention_days", 14)))
            export.deleted_at = None
            previous = job.state
            job.state = "completed"
            job.progress = 100
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = None
            job.error_message = None
            item.state = "rendered"
            publish = item.publish_job
            if publish and publish.state != "published":
                publish.state = (
                    "waiting_for_upload"
                    if item.batch.destination_channel.oauth_status == "connected"
                    else "blocked_not_connected"
                )
            channel = item.batch.destination_channel
            if channel.next_publish_at is None:
                channel.next_publish_at = utcnow()
            db.add(AutomationEvent(
                aggregate_type="render_job", aggregate_id=job.id, batch_item_id=item.id,
                event_type="render_job.completed", from_state=previous, to_state="completed",
                details={
                    "output_asset_id": export.id,
                    "output_name": destination.name,
                    "size_bytes": export.size_bytes,
                    "source_retained": source.exists(),
                },
            ))
            self._update_batch_state(db, item.batch_id)

    def _fail(self, job_id: str, stage: str, error: Exception) -> None:
        state = "download_failed" if stage == "download" else "render_failed"
        with SessionLocal.begin() as db:
            job = db.get(RenderJob, job_id)
            if not job:
                return
            previous = job.state
            job.state = state
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = state
            job.error_message = str(error)[-4000:]
            job.batch_item.state = "failed"
            if stage == "download":
                _asset(db, job.batch_item_id, "source").state = "failed"
            db.add(AutomationEvent(
                aggregate_type="render_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
                event_type=f"render_job.{state}", from_state=previous, to_state=state,
                details={"error": job.error_message, "source_files_deleted": False},
            ))
            self._update_batch_state(db, job.batch_item.batch_id)

    def _transition(
        self,
        job_id: str,
        state: str,
        item_state: str,
        progress: int,
        event_type: str | None = None,
    ) -> None:
        with SessionLocal.begin() as db:
            job = db.get(RenderJob, job_id)
            if not job:
                return
            previous = job.state
            job.state = state
            job.progress = max(job.progress, progress)
            job.batch_item.state = item_state
            if event_type and (previous != state or event_type.endswith("started")):
                db.add(AutomationEvent(
                    aggregate_type="render_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
                    event_type=event_type, from_state=previous, to_state=state,
                    details={"progress": job.progress},
                ))

    @staticmethod
    def _update_batch_state(db, batch_id: str) -> None:
        batch = db.get(UrlBatch, batch_id)
        if not batch:
            return
        states = [item.state for item in batch.items if item.validation_status == "accepted"]
        previous = batch.state
        if states and all(state == "rendered" for state in states):
            batch.state = "rendered_awaiting_upload"
        elif states and all(state == "failed" for state in states):
            batch.state = "failed"
        elif any(state == "failed" for state in states):
            batch.state = "partially_failed"
        else:
            batch.state = "processing"
        if batch.state != previous:
            db.add(AutomationEvent(
                aggregate_type="url_batch", aggregate_id=batch.id,
                event_type="url_batch.state_changed", from_state=previous, to_state=batch.state,
                details={},
            ))


def retry_render_job(db, job_id: str) -> dict[str, Any]:
    job = db.get(RenderJob, job_id)
    if not job:
        raise ValueError("Render job not found")
    if job.state not in FAILED_STATES:
        raise ValueError("Only failed automation jobs can be retried")
    previous = job.state
    job.state = "retry_requested"
    job.progress = 0
    job.error_code = None
    job.error_message = None
    job.batch_item.state = "queued"
    job.batch_item.batch.state = "processing"
    source = db.scalar(select(MediaAsset).where(
        MediaAsset.batch_item_id == job.batch_item_id,
        MediaAsset.kind == "source",
    ))
    if source and source.state == "failed":
        source.state = "pending"
    db.add(AutomationEvent(
        aggregate_type="render_job", aggregate_id=job.id, batch_item_id=job.batch_item_id,
        event_type="render_job.retry_requested", from_state=previous, to_state="retry_requested",
        details={"next_attempt": job.attempts + 1},
    ))
    db.commit()
    return {"id": job.id, "state": job.state}


def _asset(db, item_id: str, kind: str) -> MediaAsset:
    asset = db.scalar(select(MediaAsset).where(
        MediaAsset.batch_item_id == item_id,
        MediaAsset.kind == kind,
    ))
    if not asset:
        asset = MediaAsset(batch_item_id=item_id, kind=kind, state="pending")
        db.add(asset)
        db.flush()
    return asset


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-") or "untitled"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.metadata.json")


def _read_metadata(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_metadata_path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    target = _metadata_path(path)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _youtube_metadata(info_path: Path, url: str, fallback_title: str) -> dict[str, Any]:
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            info = {}
    except (OSError, json.JSONDecodeError):
        info = {}

    def text(key: str, fallback: str = "") -> str:
        value = info.get(key)
        return str(value).strip() if value is not None else fallback

    def text_list(key: str) -> list[str]:
        value = info.get(key)
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []

    return {
        "provider": "youtube",
        "video_id": text("id"),
        "source_url": text("webpage_url", url) or url,
        "title": text("title", fallback_title) or fallback_title,
        "description": text("description"),
        "tags": text_list("tags"),
        "categories": text_list("categories"),
        "channel": text("channel") or text("uploader"),
        "channel_id": text("channel_id") or text("uploader_id"),
        "upload_date": text("upload_date"),
        "captured_at": utcnow().isoformat(),
    }
