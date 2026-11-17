from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from .urls import VIDEO_ID


CHANNEL_PATH_PREFIXES = {"channel", "c", "user"}
CHANNEL_TABS = {"videos", "shorts", "streams"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


class SourceChannelDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveredVideo:
    video_id: str
    title: str
    url: str
    thumbnail_url: str


@dataclass(frozen=True)
class DiscoveredChannel:
    channel_id: str | None
    title: str
    videos: list[DiscoveredVideo]


def normalize_source_channel_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urlparse(candidate)
    except ValueError as error:
        raise ValueError("Enter a valid YouTube channel URL") from error
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise ValueError("Only YouTube channel URLs are supported")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError("Enter a YouTube channel or @handle URL")
    is_handle = parts[0].startswith("@") and len(parts[0]) > 1
    is_legacy = parts[0] in CHANNEL_PATH_PREFIXES and len(parts) >= 2
    if not is_handle and not is_legacy:
        raise ValueError("Enter a YouTube channel or @handle URL, not a video or playlist URL")
    base_length = 1 if is_handle else 2
    tab = parts[base_length] if len(parts) > base_length else None
    if tab and tab not in CHANNEL_TABS:
        raise ValueError("The source URL must point to a channel Videos, Shorts, or Streams tab")
    normalized_parts = parts[:base_length]
    path = "/" + "/".join(normalized_parts)
    return urlunparse(("https", "www.youtube.com", path, "", "", ""))


def discover_source_channel(source_channel_url: str, timeout_seconds: int = 300) -> DiscoveredChannel:
    normalized = normalize_source_channel_url(source_channel_url)
    payloads: list[dict] = []
    errors: list[str] = []
    for tab in ("videos", "shorts", "streams"):
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--ignore-errors",
            "--socket-timeout",
            "30",
            "--retries",
            "3",
            "--",
            f"{normalized}/{tab}",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"The {tab} tab timed out")
            continue
        if result.returncode:
            message = next(
                (line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()),
                f"The {tab} tab could not be read",
            )
            errors.append(message[-1000:])
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            errors.append(f"The {tab} tab returned invalid metadata")
            continue
        if isinstance(payload, dict):
            payloads.append(payload)

    videos: list[DiscoveredVideo] = []
    seen: set[str] = set()
    for payload in payloads:
        entries = payload.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            video_id = str(entry.get("id") or entry.get("url") or "").strip()
            if not VIDEO_ID.fullmatch(video_id) or video_id in seen:
                continue
            seen.add(video_id)
            videos.append(DiscoveredVideo(
                video_id=video_id,
                title=str(entry.get("title") or video_id).strip(),
                url=f"https://www.youtube.com/watch?v={video_id}",
                thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            ))
    if not videos:
        detail = errors[-1] if errors else "No public videos were found across the channel tabs"
        raise SourceChannelDiscoveryError(detail)
    identity = payloads[0] if payloads else {}
    channel_id = str(identity.get("channel_id") or identity.get("uploader_id") or "").strip() or None
    title = str(identity.get("channel") or identity.get("uploader") or identity.get("title") or normalized).strip()
    return DiscoveredChannel(channel_id=channel_id, title=title, videos=videos)
