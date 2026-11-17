from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from automation.database import database_ready
from automation.publisher import YouTubePublisher
from automation.router import router as automation_router
from automation.worker import AutomationWorker
from beat_engine import BeatGenerationCancelled, analyze_audio, ensure_default_beats, generate_beat, list_beats
from processor import (
    OUTPUT_PROFILES, FFmpegError, ProcessingCancelled, clean_partial, output_dimensions, probe, process_video,
)

INPUT_DIR = Path(os.getenv("INPUT_DIR", "/app/input"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/app/output"))
TEMPLATE_DIR = Path(os.getenv("TEMPLATE_DIR", "/app/templates"))
PREVIEW_DIR = Path(os.getenv("PREVIEW_DIR", "/app/previews"))
PROJECT_DIR = Path(os.getenv("PROJECT_DIR", str(TEMPLATE_DIR / "projects")))
BEAT_DIR = TEMPLATE_DIR / "assets" / "beats"
ANALYSIS_DIR = TEMPLATE_DIR / "assets" / "analysis"
for directory in (INPUT_DIR, OUTPUT_DIR, TEMPLATE_DIR, PREVIEW_DIR, TEMPLATE_DIR / "assets", PROJECT_DIR, BEAT_DIR, ANALYSIS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local YouTube Remix Studio", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(automation_router)


@app.get("/", include_in_schema=False)
async def open_frontend() -> RedirectResponse:
    return RedirectResponse(os.getenv("FRONTEND_URL", "http://localhost:3001"), status_code=307)

jobs: dict[str, dict[str, Any]] = {}
cancel_events: dict[str, asyncio.Event] = {}
queue_lock = asyncio.Lock()
download_jobs: dict[str, dict[str, Any]] = {}
download_cancel_events: dict[str, asyncio.Event] = {}
download_processes: dict[str, asyncio.subprocess.Process] = {}
download_tasks: set[asyncio.Task] = set()
download_lock = asyncio.Lock()
analysis_jobs: dict[str, dict[str, Any]] = {}
analysis_cancel_events: dict[str, asyncio.Event] = {}
analysis_tasks: set[asyncio.Task] = set()
generation_jobs: dict[str, dict[str, Any]] = {}
generation_cancel_events: dict[str, threading.Event] = {}
generation_tasks: set[asyncio.Task] = set()
automation_worker = AutomationWorker(INPUT_DIR, OUTPUT_DIR, TEMPLATE_DIR)
automation_worker_task: asyncio.Task | None = None
youtube_publisher = YouTubePublisher(INPUT_DIR, OUTPUT_DIR)
youtube_publisher_task: asyncio.Task | None = None


class ProcessRequest(BaseModel):
    template_id: str
    video_ids: list[str] | None = None
    settings: dict[str, Any] | None = None
    job_kind: Literal["process", "export"] = "process"


class TemplatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    settings: dict[str, Any]


class PreviewPayload(BaseModel):
    settings: dict[str, Any] | None = None


class YouTubeDownloadRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    data: dict[str, Any]


class BeatGenerateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    bpm: float = Field(default=100, ge=60, le=180)
    bars: int = Field(default=8, ge=1, le=32)
    intensity: float = Field(default=.55, ge=0, le=1)
    swing: float = Field(default=0, ge=0, le=.45)
    density: float = Field(default=.55, ge=0, le=1)
    bass_level: float = Field(default=.55, ge=0, le=1)
    percussion_level: float = Field(default=.65, ge=0, le=1)
    seed: int = Field(default=1001, ge=0, le=2_147_483_647)


class BeatAnalysisRequest(BaseModel):
    force: bool = False


@app.on_event("startup")
async def prepare_original_beat_library() -> None:
    global automation_worker_task, youtube_publisher_task
    await asyncio.to_thread(ensure_default_beats, BEAT_DIR)
    cutoff = time.time() - 24 * 60 * 60
    for path in PREVIEW_DIR.glob("preview-*.mp4"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue
    if os.getenv("AUTOMATION_WORKER_ENABLED", "true").lower() not in {"0", "false", "no"}:
        automation_worker_task = asyncio.create_task(automation_worker.run(), name="automation-worker")
    if os.getenv("YOUTUBE_PUBLISHING_ENABLED", "true").lower() not in {"0", "false", "no"}:
        youtube_publisher_task = asyncio.create_task(youtube_publisher.run(), name="youtube-publisher")


@app.on_event("shutdown")
async def stop_automation_worker() -> None:
    automation_worker.stop()
    youtube_publisher.stop()
    tasks = [task for task in (automation_worker_task, youtube_publisher_task) if task]
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=5)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def safe_name(value: str, extension: str = "") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-")
    return (cleaned or "untitled") + extension


def unique_output_name(source: Path, template_id: str, template: dict | None = None) -> str:
    """Build a readable filename that cannot collide with a saved or queued export."""
    while True:
        prefix = f"EXP-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3).upper()}"
        profile_id = str((template or {}).get("output_profile", {}).get("id") or "youtube-shorts")
        suffix = "short" if profile_id == "youtube-shorts" else "long-form"
        candidate = f"{prefix}_{safe_name(source.stem)}-{safe_name(template_id)}-{suffix}.mp4"
        already_queued = any(job.get("target_output") == candidate for job in jobs.values())
        if not already_queued and not (OUTPUT_DIR / candidate).exists():
            return candidate


def validate_youtube_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    valid_host = hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com")
    if parsed.scheme not in {"http", "https"} or not valid_host:
        raise HTTPException(422, "Enter a valid YouTube or youtu.be video URL")
    return url


def cleanup_download_files(prefix: str) -> None:
    for path in INPUT_DIR.glob(f"{prefix}*"):
        if path.is_file():
            path.unlink(missing_ok=True)


def media_metadata_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.metadata.json")


def load_media_metadata(path: Path) -> dict[str, Any] | None:
    metadata_path = media_metadata_path(path)
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_media_metadata(path: Path, metadata: dict[str, Any]) -> None:
    metadata_path = media_metadata_path(path)
    temporary_path = metadata_path.with_name(f".{metadata_path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temporary_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(metadata_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def youtube_metadata(info: dict[str, Any], url: str, fallback_title: str) -> dict[str, Any]:
    def text(key: str, fallback: str = "") -> str:
        value = info.get(key)
        return str(value).strip() if value is not None else fallback

    def text_list(key: str) -> list[str]:
        value = info.get(key)
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

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
        "captured_at": time.time(),
    }


async def terminate_download_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), timeout=4)
    except (ProcessLookupError, asyncio.TimeoutError):
        if process.returncode is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            await process.wait()


async def read_download_output(identifier: str, stream: asyncio.StreamReader, errors: list[str]) -> None:
    while line_bytes := await stream.readline():
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        job = download_jobs.get(identifier)
        if not job:
            continue
        if line.startswith("PROGRESS|"):
            fields = line.split("|", 3)
            try:
                progress = float(fields[1].strip().replace("%", ""))
            except (IndexError, ValueError):
                progress = job.get("progress", 0)
            job.update(
                status="downloading" if progress < 100 else "postprocessing",
                progress=max(0.0, min(round(progress, 1), 100.0)),
                speed=fields[2].strip() if len(fields) > 2 else "",
                eta=fields[3].strip() if len(fields) > 3 else "",
                message="Downloading" if progress < 100 else "Preparing MP4",
            )
        elif line.startswith("TITLE|"):
            job.update(title=line.partition("|")[2].strip(), message="Starting download")
        elif line.startswith("DONE|"):
            downloaded = Path(line.partition("|")[2].strip())
            job.update(filename=downloaded.name, progress=100.0, message="Finalizing")
        elif "ERROR:" in line or "WARNING:" in line:
            errors.append(line)
            del errors[:-30]


async def run_youtube_download(identifier: str, url: str, prefix: str) -> None:
    cancel_event = download_cancel_events[identifier]
    async with download_lock:
        job = download_jobs[identifier]
        if cancel_event.is_set():
            job.update(status="cancelled", message="Cancelled", finished_at=time.time())
            cleanup_download_files(prefix)
            return

        job.update(status="downloading", message="Connecting to YouTube", started_at=time.time())
        output_template = str(INPUT_DIR / f"{prefix}%(title).120B-%(id)s.%(ext)s")
        command = [
            sys.executable, "-m", "yt_dlp",
            "--newline", "--no-colors", "--no-playlist", "--windows-filenames",
            "--js-runtimes", "deno", "--progress",
            "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "3",
            "--file-access-retries", "3", "--socket-timeout", "30",
            "--retry-sleep", "http:exp=1:8", "--retry-sleep", "fragment:exp=1:8",
            "--retry-sleep", "extractor:exp=1:8",
            "--progress-template", "download:PROGRESS|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
            "--print", "before_dl:TITLE|%(title)s",
            "--print", "after_move:DONE|%(filepath)s",
            "--write-info-json",
            "--merge-output-format", "mp4", "--remux-video", "mp4",
            "-f", (
                "bv[ext=mp4][vcodec^=avc][protocol=https]+ba[ext=m4a][protocol=https]/"
                "bv[ext=mp4][protocol=https]+ba[ext=m4a][protocol=https]/"
                "b[ext=mp4][protocol=https]/bv[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv+ba/b"
            ),
            "-o", output_template, "--", url,
        ]
        errors: list[str] = []
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            download_processes[identifier] = process
            assert process.stdout and process.stderr
            await asyncio.gather(
                read_download_output(identifier, process.stdout, errors),
                read_download_output(identifier, process.stderr, errors),
                process.wait(),
            )
            if cancel_event.is_set():
                cleanup_download_files(prefix)
                job.update(status="cancelled", message="Cancelled", error=None, finished_at=time.time())
                return
            if process.returncode:
                raise RuntimeError(errors[-1] if errors else f"yt-dlp exited with code {process.returncode}")

            filename = job.get("filename")
            destination = INPUT_DIR / filename if filename else None
            if not destination or not destination.exists():
                candidates = sorted(INPUT_DIR.glob(f"{prefix}*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
                destination = candidates[0] if candidates else None
            if not destination or not destination.exists() or destination.suffix.lower() != ".mp4":
                raise RuntimeError("The download completed without producing an MP4 file")

            info: dict[str, Any] = {}
            info_paths = sorted(
                INPUT_DIR.glob(f"{prefix}*.info.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if info_paths:
                try:
                    loaded_info = json.loads(info_paths[0].read_text(encoding="utf-8"))
                    if isinstance(loaded_info, dict):
                        info = loaded_info
                except (OSError, json.JSONDecodeError):
                    info = {}
            metadata = youtube_metadata(info, url, str(job.get("title") or destination.stem))
            write_media_metadata(destination, metadata)
            for info_path in info_paths:
                info_path.unlink(missing_ok=True)
            job.update(
                status="completed", progress=100.0, message="Added to Media", filename=destination.name,
                size=destination.stat().st_size, title=metadata["title"], description=metadata["description"],
                source_metadata=metadata, finished_at=time.time(), error=None,
            )
        except Exception as error:
            cleanup_download_files(prefix)
            if cancel_event.is_set():
                job.update(status="cancelled", message="Cancelled", error=None, finished_at=time.time())
            else:
                job.update(status="failed", message="Download failed", error=str(error)[-2000:], finished_at=time.time())
        finally:
            download_processes.pop(identifier, None)
            download_cancel_events.pop(identifier, None)


def video_id(path: Path) -> str:
    relative = path.resolve().relative_to(INPUT_DIR.resolve()).as_posix()
    return hashlib.sha1(relative.encode()).hexdigest()[:16]


def find_videos() -> list[Path]:
    return sorted(
        (path for path in INPUT_DIR.rglob("*") if path.is_file() and path.suffix.lower() == ".mp4"),
        key=lambda path: path.relative_to(INPUT_DIR).as_posix().lower(),
    )


def get_video(identifier: str) -> Path:
    for path in find_videos():
        if video_id(path) == identifier:
            return path
    raise HTTPException(404, "Video not found. Refresh the input list and try again.")


def template_path(identifier: str) -> Path:
    return TEMPLATE_DIR / f"{safe_name(identifier)}.json"


def load_template(identifier: str) -> dict:
    path = template_path(identifier)
    if not path.exists():
        raise HTTPException(404, "Template not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("output", None)
        return data
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(500, f"Template is invalid: {error}") from error


def secure_child(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    try:
        path = (root / relative.replace("\\", "/")).resolve()
        resolved_root = root.resolve()
        if path != resolved_root and resolved_root not in path.parents:
            raise ValueError
    except (OSError, ValueError):
        raise HTTPException(400, "Invalid asset path") from None
    if must_exist and (not path.exists() or not path.is_file()):
        raise HTTPException(404, "Asset not found")
    return path


def project_path(identifier: str) -> Path:
    return PROJECT_DIR / f"{safe_name(identifier)}.json"


def analysis_cache_path(source: Path) -> Path:
    fingerprint = hashlib.sha256(
        f"{source.resolve()}:{source.stat().st_size}:{source.stat().st_mtime_ns}".encode()
    ).hexdigest()[:24]
    return ANALYSIS_DIR / f"{fingerprint}.json"


@app.get("/api/health")
async def health() -> dict:
    db_ready = database_ready()
    return {
        "status": "ok" if db_ready else "degraded",
        "database": db_ready,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "yt_dlp": bool(shutil.which("yt-dlp")),
        "automation_worker": bool(automation_worker_task and not automation_worker_task.done()),
        "youtube_publisher": bool(youtube_publisher_task and not youtube_publisher_task.done()),
    }


@app.get("/api/videos")
async def list_videos() -> list[dict]:
    result = []
    for path in find_videos():
        identifier = video_id(path)
        try:
            info = await probe(path)
            metadata = {
                "duration": round(info["duration"], 2), "width": info["width"], "height": info["height"],
                "fps": round(info["fps"], 3),
            }
        except Exception as error:
            metadata = {"duration": 0, "width": 0, "height": 0, "probe_error": str(error)}
        result.append({
            "id": identifier,
            "name": path.name,
            "relative_path": path.relative_to(INPUT_DIR).as_posix(),
            "size": path.stat().st_size,
            "source_metadata": load_media_metadata(path),
            **metadata,
            "job": jobs.get(identifier, {"status": "idle", "progress": 0, "message": "Ready"}),
        })
    return result


@app.post("/api/videos/upload")
async def upload_videos(files: list[UploadFile] = File(...)) -> dict:
    saved = []
    rejected = []
    for upload in files:
        if not upload.filename or Path(upload.filename).suffix.lower() != ".mp4":
            rejected.append(upload.filename or "unnamed file")
            continue
        destination = INPUT_DIR / safe_name(Path(upload.filename).name)
        counter = 1
        while destination.exists():
            destination = INPUT_DIR / f"{destination.stem}-{counter}{destination.suffix}"
            counter += 1
        with destination.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                target.write(chunk)
        saved.append(destination.name)
    return {"saved": saved, "rejected": rejected}


@app.get("/api/youtube-downloads")
async def list_youtube_downloads() -> list[dict]:
    return sorted(
        (copy.deepcopy(job) for job in download_jobs.values()),
        key=lambda job: job.get("created_at", 0),
        reverse=True,
    )


@app.post("/api/youtube-downloads")
async def start_youtube_download(payload: YouTubeDownloadRequest) -> dict:
    url = validate_youtube_url(payload.url)
    identifier = secrets.token_hex(8)
    prefix = f"YT-{identifier[:8].upper()}-"
    download_jobs[identifier] = {
        "id": identifier, "url": url, "status": "queued", "progress": 0.0,
        "speed": "", "eta": "", "title": "YouTube video", "description": "", "source_metadata": None,
        "filename": None,
        "message": "Waiting to download", "error": None, "created_at": time.time(),
    }
    download_cancel_events[identifier] = asyncio.Event()
    task = asyncio.create_task(run_youtube_download(identifier, url, prefix))
    download_tasks.add(task)
    task.add_done_callback(download_tasks.discard)
    return copy.deepcopy(download_jobs[identifier])


@app.post("/api/youtube-downloads/{identifier}/cancel")
async def cancel_youtube_download(identifier: str) -> dict:
    job = download_jobs.get(identifier)
    if not job:
        raise HTTPException(404, "YouTube download not found")
    if job.get("status") not in {"queued", "downloading", "postprocessing", "cancelling"}:
        raise HTTPException(409, "This download is no longer active")
    event = download_cancel_events.setdefault(identifier, asyncio.Event())
    event.set()
    job.update(status="cancelling", message="Stopping download")
    process = download_processes.get(identifier)
    if process:
        await terminate_download_process(process)
    return {"id": identifier, "status": "cancelling"}


@app.get("/api/videos/{identifier}/stream")
async def stream_video(identifier: str) -> FileResponse:
    path = get_video(identifier)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/videos/{identifier}/poster")
async def video_poster(identifier: str) -> FileResponse:
    source = get_video(identifier)
    destination = PREVIEW_DIR / f"poster-{identifier}-{source.stat().st_mtime_ns}.jpg"
    cache_hit = destination.exists()
    if not cache_hit:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "0.05", "-i", str(source), "-frames:v", "1",
            "-vf", "scale=540:-2:flags=lanczos", "-q:v", "4", str(destination),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not destination.exists():
            destination.unlink(missing_ok=True)
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise HTTPException(422, f"Could not create video poster: {detail[-1000:]}")
    return FileResponse(destination, media_type="image/jpeg")


@app.delete("/api/videos/{identifier}")
async def delete_video(identifier: str) -> dict:
    path = get_video(identifier)
    cache_path = analysis_cache_path(path)
    job = jobs.get(identifier) or {}
    if job.get("status") in {"queued", "processing", "cancelling"}:
        raise HTTPException(409, "This video is currently processing and cannot be deleted")
    try:
        path.unlink()
        cache_path.unlink(missing_ok=True)
        media_metadata_path(path).unlink(missing_ok=True)
    except OSError as error:
        raise HTTPException(500, f"Could not delete input video: {error}") from error
    jobs.pop(identifier, None)
    return {"deleted": identifier, "name": path.name}


@app.get("/api/templates")
async def list_templates() -> list[dict]:
    templates = []
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("output", None)
            templates.append({"id": path.stem, **data})
        except (OSError, json.JSONDecodeError):
            templates.append({"id": path.stem, "name": path.stem, "invalid": True})
    return templates


@app.post("/api/templates")
async def create_template(payload: TemplatePayload) -> dict:
    identifier = safe_name(payload.name.lower())
    path = template_path(identifier)
    if path.exists():
        raise HTTPException(409, "A template with this name already exists")
    settings = copy.deepcopy(payload.settings)
    settings.pop("output", None)
    data = {"name": payload.name.strip(), **settings}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"id": identifier, **data}


@app.put("/api/templates/{identifier}")
async def update_template(identifier: str, payload: TemplatePayload) -> dict:
    path = template_path(identifier)
    if not path.exists():
        raise HTTPException(404, "Template not found")
    settings = copy.deepcopy(payload.settings)
    settings.pop("output", None)
    data = {"name": payload.name.strip(), **settings}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"id": identifier, **data}


@app.delete("/api/templates/{identifier}")
async def delete_template(identifier: str) -> dict:
    path = template_path(identifier)
    if not path.exists():
        raise HTTPException(404, "Template not found")
    path.unlink()
    return {"deleted": identifier}


@app.get("/api/output-profiles")
async def list_output_profiles() -> list[dict]:
    return list(OUTPUT_PROFILES.values())


@app.get("/api/projects")
async def list_projects() -> list[dict]:
    results = []
    for path in sorted(PROJECT_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append({"id": path.stem, "name": data.get("name", path.stem), "updated_at": path.stat().st_mtime})
        except (OSError, json.JSONDecodeError):
            continue
    return results


@app.get("/api/projects/{identifier}")
async def get_project(identifier: str) -> dict:
    path = project_path(identifier)
    if not path.exists():
        raise HTTPException(404, "Project not found")
    try:
        return {"id": path.stem, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(500, f"Project is invalid: {error}") from error


@app.post("/api/projects")
async def save_project(payload: ProjectPayload) -> dict:
    identifier = safe_name(payload.name.lower())
    path = project_path(identifier)
    data = {"schema_version": 1, "name": payload.name.strip(), "updated_at": time.time(), **copy.deepcopy(payload.data)}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)
    return {"id": identifier, **data}


@app.put("/api/projects/{identifier}")
async def update_project(identifier: str, payload: ProjectPayload) -> dict:
    path = project_path(identifier)
    if not path.exists():
        raise HTTPException(404, "Project not found")
    data = {"schema_version": 1, "name": payload.name.strip(), "updated_at": time.time(), **copy.deepcopy(payload.data)}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)
    return {"id": path.stem, **data}


@app.delete("/api/projects/{identifier}")
async def delete_project(identifier: str) -> dict:
    path = project_path(identifier)
    if not path.exists():
        raise HTTPException(404, "Project not found")
    path.unlink()
    return {"deleted": identifier}


@app.get("/api/beats")
async def get_beat_library() -> list[dict]:
    return await asyncio.to_thread(list_beats, BEAT_DIR)


@app.post("/api/beats/generate")
async def create_original_beat(payload: BeatGenerateRequest) -> dict:
    identifier = secrets.token_hex(8)
    generation_jobs[identifier] = {
        "id": identifier, "status": "queued", "progress": 0, "message": "Waiting to synthesize",
        "result": None, "error": None, "created_at": time.time(),
    }
    generation_cancel_events[identifier] = threading.Event()
    task = asyncio.create_task(run_beat_generation(identifier, payload.model_dump()))
    generation_tasks.add(task)
    task.add_done_callback(generation_tasks.discard)
    return copy.deepcopy(generation_jobs[identifier])


async def run_beat_generation(identifier: str, settings: dict[str, Any]) -> None:
    job = generation_jobs[identifier]
    event = generation_cancel_events[identifier]

    def update(progress: float, message: str) -> None:
        if not event.is_set():
            job.update(status="generating", progress=round(progress, 1), message=message)

    try:
        result = await asyncio.to_thread(generate_beat, BEAT_DIR, **settings, cancel_event=event, progress=update)
        job.update(status="completed", progress=100, message="Beat generation complete", result=result, finished_at=time.time())
    except BeatGenerationCancelled:
        job.update(status="cancelled", message="Beat generation cancelled", result=None, finished_at=time.time())
    except Exception as error:
        job.update(status="failed", message="Beat generation failed", error=str(error)[-2000:], finished_at=time.time())
    finally:
        generation_cancel_events.pop(identifier, None)


@app.get("/api/beats/generation/{identifier}")
async def get_beat_generation(identifier: str) -> dict:
    job = generation_jobs.get(identifier)
    if not job:
        raise HTTPException(404, "Beat generation job not found")
    return copy.deepcopy(job)


@app.post("/api/beats/generation/{identifier}/cancel")
async def cancel_beat_generation(identifier: str) -> dict:
    job = generation_jobs.get(identifier)
    if not job:
        raise HTTPException(404, "Beat generation job not found")
    if job.get("status") not in {"queued", "generating"}:
        raise HTTPException(409, "This generation is no longer active")
    generation_cancel_events.setdefault(identifier, threading.Event()).set()
    job.update(status="cancelling", message="Stopping beat generation")
    return copy.deepcopy(job)


@app.delete("/api/beats/{identifier}")
async def delete_generated_beat(identifier: str) -> dict:
    safe_identifier = safe_name(identifier)
    folder = (BEAT_DIR / safe_identifier).resolve()
    if folder.parent != BEAT_DIR.resolve() or not folder.exists() or not folder.is_dir():
        raise HTTPException(404, "Generated beat not found")
    try:
        for path in folder.iterdir():
            if path.is_file():
                path.unlink()
        folder.rmdir()
    except OSError as error:
        raise HTTPException(500, f"Could not delete generated beat: {error}") from error
    return {"deleted": safe_identifier}


async def run_beat_analysis(identifier: str, source: Path, cache_path: Path) -> None:
    job = analysis_jobs[identifier]
    event = analysis_cancel_events[identifier]

    async def update(progress: float, message: str) -> None:
        if not event.is_set():
            job.update(status="analyzing", progress=round(progress, 1), message=message)

    try:
        result = await analyze_audio(source, cache_path, update, event)
        if event.is_set():
            cache_path.unlink(missing_ok=True)
            job.update(status="cancelled", message="Analysis cancelled", result=None, finished_at=time.time())
        else:
            job.update(status="completed", progress=100, message="Beat analysis complete", result=result, finished_at=time.time())
    except asyncio.CancelledError:
        cache_path.unlink(missing_ok=True)
        job.update(status="cancelled", message="Analysis cancelled", result=None, finished_at=time.time())
    except Exception as error:
        cache_path.unlink(missing_ok=True)
        job.update(status="failed", message="Beat analysis failed", error=str(error)[-2000:], finished_at=time.time())
    finally:
        analysis_cancel_events.pop(identifier, None)


@app.post("/api/beat-analysis/{video_identifier}")
async def start_beat_analysis(video_identifier: str, payload: BeatAnalysisRequest | None = None) -> dict:
    source = get_video(video_identifier)
    cache_path = analysis_cache_path(source)
    if payload and payload.force:
        cache_path.unlink(missing_ok=True)
    active = next((job for job in analysis_jobs.values() if job.get("video_id") == video_identifier and job.get("status") in {"queued", "analyzing"}), None)
    if active:
        return copy.deepcopy(active)
    identifier = secrets.token_hex(8)
    analysis_jobs[identifier] = {
        "id": identifier, "video_id": video_identifier, "source": source.name, "status": "queued",
        "progress": 0, "message": "Waiting to analyze", "result": None, "error": None, "created_at": time.time(),
    }
    analysis_cancel_events[identifier] = asyncio.Event()
    task = asyncio.create_task(run_beat_analysis(identifier, source, cache_path))
    analysis_tasks.add(task)
    task.add_done_callback(analysis_tasks.discard)
    return copy.deepcopy(analysis_jobs[identifier])


@app.get("/api/beat-analysis/{identifier}")
async def get_beat_analysis(identifier: str) -> dict:
    job = analysis_jobs.get(identifier)
    if not job:
        raise HTTPException(404, "Beat analysis job not found")
    return copy.deepcopy(job)


@app.post("/api/beat-analysis/{identifier}/cancel")
async def cancel_beat_analysis(identifier: str) -> dict:
    job = analysis_jobs.get(identifier)
    if not job:
        raise HTTPException(404, "Beat analysis job not found")
    if job.get("status") not in {"queued", "analyzing"}:
        raise HTTPException(409, "This analysis is no longer active")
    analysis_cancel_events.setdefault(identifier, asyncio.Event()).set()
    job.update(status="cancelling", message="Stopping beat analysis")
    return {"id": identifier, "status": "cancelling"}


@app.post("/api/assets")
async def upload_asset(file: UploadFile = File(...)) -> dict:
    allowed = {".png", ".jpg", ".jpeg", ".mp3", ".wav", ".m4a", ".aac", ".mp4", ".mov"}
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"Unsupported asset type. Allowed: {', '.join(sorted(allowed))}")
    destination = TEMPLATE_DIR / "assets" / safe_name(Path(file.filename or "asset").name)
    with destination.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            target.write(chunk)
    return {"path": f"assets/{destination.name}", "name": destination.name}


@app.get("/api/assets/{asset_path:path}")
async def get_asset(asset_path: str) -> FileResponse:
    path = secure_child(TEMPLATE_DIR / "assets", asset_path)
    return FileResponse(path)


async def run_batch(paths: list[Path], template: dict, template_id: str) -> None:
    async with queue_lock:
        for source in paths:
            identifier = video_id(source)
            cancel_event = cancel_events.setdefault(identifier, asyncio.Event())
            job_kind = jobs.get(identifier, {}).get("kind", "process")
            if cancel_event.is_set():
                jobs[identifier].update(status="cancelled", message="Stopped", error=None, finished_at=time.time())
                cancel_events.pop(identifier, None)
                continue
            output_name = jobs.get(identifier, {}).get("target_output") or unique_output_name(source, template_id, template)
            destination = OUTPUT_DIR / output_name
            working_destination = OUTPUT_DIR / f".{destination.stem}.rendering.mp4"
            clean_partial(working_destination)

            async def update(progress: float, message: str, current_id: str = identifier) -> None:
                if not cancel_event.is_set():
                    jobs[current_id].update(progress=min(round(progress, 1), 99.0), status="processing", message=message)

            jobs[identifier] = {
                "status": "processing", "progress": 0, "message": "Starting", "output": None,
                "target_output": output_name, "error": None, "source": source.name,
                "template": template.get("name", template_id), "kind": job_kind,
            }
            try:
                free_bytes = shutil.disk_usage(OUTPUT_DIR).free
                estimated_bytes = max(source.stat().st_size * 2, 256 * 1024 * 1024)
                if free_bytes < estimated_bytes:
                    raise FFmpegError(
                        f"Not enough disk space: about {estimated_bytes / 1024**3:.1f} GB is required, "
                        f"but only {free_bytes / 1024**3:.1f} GB is free"
                    )
                await process_video(source, working_destination, template, TEMPLATE_DIR, INPUT_DIR, update, cancel_event=cancel_event)
                if not working_destination.exists() or working_destination.stat().st_size == 0:
                    raise FFmpegError("FFmpeg finished without creating an output video")
                output_info = await probe(working_destination)
                expected_width, expected_height = await output_dimensions(source, template)
                if (
                    output_info["duration"] <= 0
                    or output_info["width"] != expected_width
                    or output_info["height"] != expected_height
                ):
                    raise FFmpegError("The rendered output failed validation")
                working_destination.replace(destination)
                source_metadata = load_media_metadata(source)
                if source_metadata:
                    output_metadata = {**source_metadata, "derived_from": source.name}
                    write_media_metadata(destination, output_metadata)
                jobs[identifier].update(
                    status="completed", progress=100, message="Finished", output=output_name, finished_at=time.time()
                )
            except ProcessingCancelled:
                clean_partial(working_destination)
                jobs[identifier].update(status="cancelled", message="Stopped", error=None, finished_at=time.time())
            except Exception as error:
                clean_partial(working_destination)
                jobs[identifier].update(status="failed", message="Failed", error=str(error)[-4000:], finished_at=time.time())
            finally:
                cancel_events.pop(identifier, None)


@app.post("/api/process")
async def start_processing(request: ProcessRequest, background_tasks: BackgroundTasks) -> dict:
    template = copy.deepcopy(request.settings) if request.settings is not None else load_template(request.template_id)
    template.pop("output", None)
    job_template_id = request.template_id
    paths = find_videos() if request.video_ids is None else [get_video(identifier) for identifier in request.video_ids]
    if not paths:
        raise HTTPException(400, "No videos selected")
    busy = [video_id(path) for path in paths if jobs.get(video_id(path), {}).get("status") in {"queued", "processing", "cancelling"}]
    if busy:
        raise HTTPException(409, "One or more selected videos are already queued")
    for index, path in enumerate(paths, start=1):
        identifier = video_id(path)
        output_name = unique_output_name(path, job_template_id, template)
        job_kind = request.job_kind
        cancel_events[identifier] = asyncio.Event()
        jobs[identifier] = {
            "status": "queued", "progress": 0, "message": f"Queued {index} of {len(paths)}",
            "output": None, "target_output": output_name, "error": None, "source": path.name,
            "template": template.get("name", request.template_id), "kind": job_kind,
        }
    background_tasks.add_task(run_batch, paths, template, job_template_id)
    return {"queued": len(paths)}


@app.post("/api/jobs/{identifier}/cancel")
async def cancel_job(identifier: str) -> dict:
    job = jobs.get(identifier)
    if not job:
        raise HTTPException(404, "Processing job not found")
    if job.get("status") not in {"queued", "processing", "cancelling"}:
        raise HTTPException(409, "This processing job is no longer active")
    cancel_event = cancel_events.setdefault(identifier, asyncio.Event())
    cancel_event.set()
    job.update(status="cancelling", message="Cancelling FFmpeg processing")
    return {"id": identifier, "status": "cancelling"}


@app.post("/api/preview/{video_identifier}/{template_identifier}")
async def render_preview(
    video_identifier: str,
    template_identifier: str,
    payload: PreviewPayload | None = None,
) -> dict:
    source = get_video(video_identifier)
    template = payload.settings if payload and payload.settings is not None else load_template(template_identifier)
    cache_key = json.dumps(template, sort_keys=True, separators=(",", ":"), default=str)
    token = hashlib.sha1(
        f"{video_identifier}:{source.stat().st_size}:{source.stat().st_mtime_ns}:{cache_key}".encode()
    ).hexdigest()[:16]
    destination = PREVIEW_DIR / f"preview-{token}.mp4"
    cache_hit = destination.exists()

    async def ignore_progress(progress: float, message: str) -> None:
        return None

    if not cache_hit:
        try:
            await process_video(source, destination, template, TEMPLATE_DIR, INPUT_DIR, ignore_progress, preview_seconds=6)
        except Exception as error:
            clean_partial(destination)
            raise HTTPException(422, f"Preview failed: {error}") from error
    return {"url": f"/api/previews/{destination.name}", "cached": cache_hit}


@app.get("/api/previews/{name}")
async def get_preview(name: str) -> FileResponse:
    path = PREVIEW_DIR / safe_name(name)
    if not path.exists():
        raise HTTPException(404, "Preview not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/outputs")
async def list_outputs() -> list[dict]:
    results = []
    finished_paths = (path for path in OUTPUT_DIR.glob("*.mp4") if not path.name.startswith("."))
    for path in sorted(finished_paths, key=lambda item: item.stat().st_mtime, reverse=True):
        matching_job = next((job for job in jobs.values() if job.get("output") == path.name), None)
        try:
            info = await probe(path)
            duration = round(info["duration"], 2)
        except Exception:
            duration = 0
        results.append({
            "name": path.name,
            "size": path.stat().st_size,
            "duration": duration,
            "modified": path.stat().st_mtime,
            "source": matching_job.get("source") if matching_job else None,
            "template": matching_job.get("template") if matching_job else None,
            "source_metadata": load_media_metadata(path),
        })
    return results


@app.get("/api/outputs/{name}")
async def get_output(name: str) -> FileResponse:
    path = OUTPUT_DIR / safe_name(name)
    if not path.exists():
        raise HTTPException(404, "Output not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.delete("/api/outputs/{name}")
async def delete_output(name: str) -> dict:
    safe_filename = safe_name(name)
    path = OUTPUT_DIR / safe_filename
    if not path.exists() or not path.is_file() or path.suffix.lower() != ".mp4":
        raise HTTPException(404, "Output not found")
    if any(
        job.get("target_output") == safe_filename and job.get("status") in {"queued", "processing"}
        for job in jobs.values()
    ):
        raise HTTPException(409, "This output is currently being written and cannot be deleted")
    try:
        path.unlink()
        media_metadata_path(path).unlink(missing_ok=True)
    except OSError as error:
        raise HTTPException(500, f"Could not delete output video: {error}") from error
    return {"deleted": safe_filename}
