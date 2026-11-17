from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
SUPPORTED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


@dataclass(frozen=True)
class ParsedUrl:
    raw_url: str
    video_id: str | None
    normalized_url: str | None
    thumbnail_url: str | None
    error: str | None


def split_url_input(value: str) -> list[str]:
    return [token.strip().strip("<>[](){}\"'") for token in re.split(r"[\s,]+", value) if token.strip()]


def parse_youtube_url(raw_url: str) -> ParsedUrl:
    candidate = raw_url.strip()
    if not candidate:
        return ParsedUrl(raw_url, None, None, None, "URL is empty")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ParsedUrl(raw_url, None, None, None, "Malformed URL")
    host = (parsed.hostname or "").lower()
    if host not in SUPPORTED_HOSTS:
        return ParsedUrl(raw_url, None, None, None, "Only YouTube video URLs are supported")

    video_id: str | None = None
    parts = [part for part in parsed.path.split("/") if part]
    if host.endswith("youtu.be") and parts:
        video_id = parts[0]
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [None])[0]
    elif parts and parts[0] in {"shorts", "embed", "live"} and len(parts) > 1:
        video_id = parts[1]

    if not video_id or not VIDEO_ID.fullmatch(video_id):
        return ParsedUrl(raw_url, None, None, None, "A valid 11-character YouTube video ID was not found")
    normalized = f"https://www.youtube.com/watch?v={video_id}"
    thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return ParsedUrl(raw_url, video_id, normalized, thumbnail, None)

