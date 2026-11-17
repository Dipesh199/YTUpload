from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

ProgressCallback = Callable[[float, str], Awaitable[None]]

OUTPUT_PROFILES = {
    "youtube-1080p": {"id": "youtube-1080p", "name": "YouTube Landscape", "width": 1920, "height": 1080, "aspect_ratio": "16:9"},
    "youtube-1440p": {"id": "youtube-1440p", "name": "YouTube 1440p", "width": 2560, "height": 1440, "aspect_ratio": "16:9"},
    "youtube-shorts": {"id": "youtube-shorts", "name": "YouTube Shorts", "width": 1080, "height": 1920, "aspect_ratio": "9:16"},
    "preserve-source": {"id": "preserve-source", "name": "Preserve Source", "width": None, "height": None, "aspect_ratio": "source"},
}


class FFmpegError(RuntimeError):
    pass


class ProcessingCancelled(RuntimeError):
    pass


_video_encoder_cache: str | None = None
_video_encoder_lock = asyncio.Lock()


async def _select_video_encoder() -> str:
    """Probe an opt-in hardware encoder once; always retain a working CPU fallback."""
    global _video_encoder_cache
    if _video_encoder_cache:
        return _video_encoder_cache
    async with _video_encoder_lock:
        if _video_encoder_cache:
            return _video_encoder_cache
        requested = os.getenv("FFMPEG_VIDEO_ENCODER", "libx264").strip().lower()
        supported = ("h264_nvenc", "h264_qsv", "h264_amf")
        candidates = supported if requested == "auto" else ((requested,) if requested in supported else ())
        for encoder in candidates:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=64x64:d=.1",
                "-frames:v", "1", "-an", "-c:v", encoder, "-f", "null", "-",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            if await process.wait() == 0:
                _video_encoder_cache = encoder
                return encoder
        _video_encoder_cache = "libx264"
        return _video_encoder_cache


def _video_encoding_args(encoder: str, fps: float) -> list[str]:
    quality = os.getenv("FFMPEG_CRF", "20")
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p5", "-cq", quality, "-b:v", "0", "-profile:v", "high"]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "medium", "-global_quality", quality, "-profile:v", "high"]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "quality", "-qp_i", quality, "-qp_p", quality, "-profile:v", "high"]
    return [
        "-c:v", "libx264", "-preset", os.getenv("FFMPEG_PRESET", "medium"),
        "-crf", quality, "-profile:v", "high", "-level", _h264_level(fps),
    ]


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=4)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _communicate_with_cancellation(
    process: asyncio.subprocess.Process,
    cancel_event: asyncio.Event | None,
) -> tuple[bytes, bytes]:
    communicate_task = asyncio.create_task(process.communicate())
    if cancel_event is None:
        return await communicate_task
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, _ = await asyncio.wait({communicate_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
    if cancel_task in done and cancel_event.is_set():
        await _terminate_process(process)
        await communicate_task
        raise ProcessingCancelled("Export stopped by user")
    cancel_task.cancel()
    await asyncio.gather(cancel_task, return_exceptions=True)
    return await communicate_task


async def probe(path: Path) -> dict:
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise FFmpegError(stderr.decode(errors="replace").strip() or "ffprobe failed")
    data = json.loads(stdout or b"{}")
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    frame_rate = "30/1"
    fps = 30.0
    for candidate in (video.get("avg_frame_rate"), video.get("r_frame_rate")):
        try:
            numerator, denominator = map(int, str(candidate).split("/", 1))
            candidate_fps = numerator / denominator
            if 1 <= candidate_fps <= 240:
                frame_rate, fps = f"{numerator}/{denominator}", candidate_fps
                break
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "frame_rate": frame_rate,
        "fps": fps,
        "has_audio": any(stream.get("codec_type") == "audio" for stream in data.get("streams", [])),
    }


def resolve_output_profile(template: dict, source_info: dict) -> dict:
    """Resolve output geometry. Missing profiles intentionally retain legacy Shorts behavior."""
    requested = template.get("output_profile") or {}
    profile_id = str(requested.get("id") or "youtube-shorts")
    base = dict(OUTPUT_PROFILES.get(profile_id, OUTPUT_PROFILES["youtube-shorts"]))
    if profile_id == "preserve-source":
        base["width"] = int(source_info["width"])
        base["height"] = int(source_info["height"])
    else:
        width = int(requested.get("width") or base["width"])
        height = int(requested.get("height") or base["height"])
        if width < 320 or height < 320 or width > 4096 or height > 4096:
            raise FFmpegError("Output dimensions must be between 320 and 4096 pixels")
        base["width"], base["height"] = width - width % 2, height - height % 2
    base["preserve_source_fps"] = requested.get("preserve_source_fps", True) is not False
    return base


async def output_dimensions(source: Path, template: dict) -> tuple[int, int]:
    info = await probe(source)
    profile = resolve_output_profile(template, info)
    return int(profile["width"]), int(profile["height"])


def _escape_filter_text(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def _wrap_text(value: str, font_size: int) -> str:
    """Wrap titles deterministically so browser and FFmpeg previews use the same lines."""
    max_characters = max(12, min(32, math.floor(900 / max(font_size, 16))))
    wrapped_paragraphs: list[str] = []
    for paragraph in value.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            wrapped_paragraphs.append("")
            continue
        lines = [words[0]]
        for word in words[1:]:
            candidate = f"{lines[-1]} {word}"
            if len(candidate) <= max_characters:
                lines[-1] = candidate
            else:
                lines.append(word)
        wrapped_paragraphs.append("\n".join(lines))
    return "\n".join(wrapped_paragraphs)


def _position(position: str, margin: int = 48) -> tuple[str, str]:
    positions = {
        "top-left": (str(margin), str(margin)),
        "top-center": ("(W-w)/2", str(margin)),
        "top-right": (f"W-w-{margin}", str(margin)),
        "center-left": (str(margin), "(H-h)/2"),
        "center": ("(W-w)/2", "(H-h)/2"),
        "center-right": (f"W-w-{margin}", "(H-h)/2"),
        "bottom-left": (str(margin), f"H-h-{margin}"),
        "bottom-center": ("(W-w)/2", f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
    }
    return positions.get(position, positions["bottom-center"])


def _drawtext_position(position: str, margin: int = 90) -> tuple[str, str]:
    positions = {
        "top-left": (str(margin), str(margin)),
        "top-center": ("(w-text_w)/2", str(margin)),
        "top-right": (f"w-text_w-{margin}", str(margin)),
        "center-left": (str(margin), "(h-text_h)/2"),
        "center": ("(w-text_w)/2", "(h-text_h)/2"),
        "center-right": (f"w-text_w-{margin}", "(h-text_h)/2"),
        "bottom-left": (str(margin), f"h-text_h-{margin}"),
        "bottom-center": ("(w-text_w)/2", f"h-text_h-{margin}"),
        "bottom-right": (f"w-text_w-{margin}", f"h-text_h-{margin}"),
    }
    return positions.get(position, positions["bottom-center"])


def _text_line_positions(position: str, margin: int, font_size: int, line_count: int) -> list[tuple[str, str]]:
    horizontal = position.rsplit("-", 1)[-1] if "-" in position else "center"
    if horizontal == "left":
        x = str(margin)
    elif horizontal == "right":
        x = f"w-text_w-{margin}"
    else:
        x = "(w-text_w)/2"

    line_height = max(font_size, round(font_size * 1.1))
    block_height = line_height * line_count
    if position.startswith("top-"):
        first_y = str(margin)
    elif position.startswith("bottom-"):
        first_y = f"h-{block_height}-{margin}"
    else:
        first_y = f"(h-{block_height})/2"
    return [(x, f"{first_y}+{index * line_height}") for index in range(line_count)]


def _timing_bounds(config: dict, duration: float) -> tuple[float, float]:
    timing = config.get("timing") or {}
    start = max(0.0, min(float(timing.get("start", 0) or 0), max(0.0, duration - 0.04)))
    raw_end = timing.get("end")
    end = duration if raw_end in (None, "") else min(duration, max(start + 0.04, float(raw_end)))
    return start, end


def _animated_drawtext_position(
    config: dict,
    start: float,
    end: float,
    base_x: str | None = None,
    base_y: str | None = None,
) -> tuple[str, str, str]:
    """Return drawtext x/y/alpha expressions for a timed title animation."""
    if base_x is None or base_y is None:
        base_x, base_y = _drawtext_position(config.get("position", "bottom-center"), int(config.get("margin", 90)))
    animation = str(config.get("animation", "fade"))
    enter = min(0.55, max(0.08, (end - start) / 3))
    fade = min(0.35, max(0.06, (end - start) / 4))
    x, y = base_x, base_y
    if animation == "slide-left":
        x = f"if(lt(t,{start + enter:.3f}),-text_w+({base_x}+text_w)*(t-{start:.3f})/{enter:.3f},{base_x})"
    elif animation == "slide-up":
        y = f"if(lt(t,{start + enter:.3f}),h+({base_y}-h)*(t-{start:.3f})/{enter:.3f},{base_y})"

    if animation == "pulse":
        alpha = f"0.78+0.22*sin(2*PI*(t-{start:.3f})*2.2)"
    else:
        alpha = (
            f"if(lt(t,{start + fade:.3f}),(t-{start:.3f})/{fade:.3f},"
            f"if(gt(t,{end - fade:.3f}),({end:.3f}-t)/{fade:.3f},1))"
        )
    return x, y, alpha


def _safe_asset(value: str | None, template_root: Path, input_root: Path) -> Path | None:
    if not value:
        return None
    raw = Path(value)
    candidates = [
        raw if raw.is_absolute() else template_root / raw,
        input_root / raw,
    ]
    roots = [template_root.resolve(), input_root.resolve()]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.exists() and any(resolved == root or root in resolved.parents for root in roots):
                return resolved
        except OSError:
            continue
    return None


async def _run_ffmpeg(
    command: list[str],
    duration: float,
    progress: ProgressCallback,
    start: float,
    span: float,
    cancel_event: asyncio.Event | None = None,
) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_chunks: list[bytes] = []

    async def read_stderr() -> None:
        assert process.stderr
        while chunk := await process.stderr.read(4096):
            stderr_chunks.append(chunk)
            if sum(map(len, stderr_chunks)) > 120_000:
                stderr_chunks.pop(0)

    stderr_task = asyncio.create_task(read_stderr())

    async def stop_when_cancelled() -> None:
        if cancel_event is None:
            return
        await cancel_event.wait()
        await _terminate_process(process)

    cancel_task = asyncio.create_task(stop_when_cancelled())
    assert process.stdout
    while line := await process.stdout.readline():
        decoded = line.decode(errors="replace").strip()
        if decoded.startswith("out_time_ms=") and duration > 0:
            try:
                seconds = int(decoded.split("=", 1)[1]) / 1_000_000
                await progress(min(99.0, start + span * min(1.0, seconds / duration)), "processing")
            except ValueError:
                pass
    return_code = await process.wait()
    await stderr_task
    cancel_task.cancel()
    await asyncio.gather(cancel_task, return_exceptions=True)
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessingCancelled("Export stopped by user")
    if return_code:
        message = b"".join(stderr_chunks).decode(errors="replace").strip()
        useful = "\n".join(message.splitlines()[-16:])
        raise FFmpegError(useful or f"FFmpeg exited with code {return_code}")


def _visual_chain(template: dict, source_info: dict | None = None) -> str:
    scale = template.get("scale") or {}
    crop = template.get("crop") or {}
    mirror = template.get("mirror") or {}
    adjustments = template.get("image_adjustments") or {}
    transforms: list[str] = []
    if mirror.get("horizontal"):
        transforms.append("hflip")
    if mirror.get("vertical"):
        transforms.append("vflip")
    profile = resolve_output_profile(template, source_info or {"width": 1080, "height": 1920})
    width, height = int(profile["width"]), int(profile["height"])
    mode = scale.get("mode", "cover")
    zoom = max(1.0, min(float(scale.get("zoom", 1.0)), 3.0))
    focal_x = max(0.0, min(float(crop.get("focal_x", 0.5)), 1.0))
    focal_y = max(0.0, min(float(crop.get("focal_y", 0.5)), 1.0))
    if mode == "contain":
        transforms.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    else:
        scaled_w = max(width, round(width * zoom))
        scaled_h = max(height, round(height * zoom))
        transforms.append(
            f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(in_w-out_w)*{focal_x:.4f}:(in_h-out_h)*{focal_y:.4f},setsar=1"
        )
    if adjustments.get("enabled"):
        contrast = max(0.5, min(float(adjustments.get("contrast", 1.0)), 2.0))
        brightness = max(-0.5, min(float(adjustments.get("brightness", 0.0)), 0.5))
        saturation = max(0.0, min(float(adjustments.get("saturation", 1.0)), 3.0))
        gamma = max(0.5, min(float(adjustments.get("gamma", 1.0)), 2.0))
        sharpness = max(0.0, min(float(adjustments.get("sharpness", 0.0)), 1.5))
        transforms.append(
            f"eq=contrast={contrast:.3f}:brightness={brightness:.3f}:"
            f"saturation={saturation:.3f}:gamma={gamma:.3f}"
        )
        if sharpness > 0:
            transforms.append(f"unsharp=5:5:{sharpness:.3f}:5:5:0")
    return ",".join(transforms)


def _effect_period(effect: dict, bpm: float) -> float:
    beats = max(1, int(effect.get("every_n_beats", 1)))
    if effect.get("trigger") == "downbeat":
        beats *= 4
    minimum_interval = max(0.0, float(effect.get("minimum_interval_ms", 0) or 0) / 1000.0)
    return max(.08, minimum_interval, 60.0 / max(1.0, bpm) * beats)


def _beat_enable(effect: dict, bpm: float, duration: float) -> str:
    start = max(0.0, float(effect.get("start", 0) or 0))
    end = duration if effect.get("end") in (None, "") else min(duration, float(effect["end"]))
    offset = float(effect.get("beat_offset", 0) or 0)
    if effect.get("trigger") == "manual":
        return f"between(t,{start:.4f},{end:.4f})"
    period = _effect_period(effect, bpm)
    active = max(.04, min(float(effect.get("duration_ms", 160)) / 1000.0, period * .8))
    return f"between(t,{start:.4f},{end:.4f})*lt(mod(max(t-{offset:.4f},0),{period:.6f}),{active:.4f})"


def _apply_beat_effects(
    filters: list[str], current_video: str, template: dict, duration: float, width: int, height: int,
) -> str:
    beat_track = template.get("beat_track") or {}
    bpm = float(beat_track.get("bpm") or 100)
    safety = template.get("motion_safety") or {}
    reduced = safety.get("reduced_flashing", True) is not False
    for number, effect in enumerate(template.get("beat_effects") or [], start=1):
        if not effect.get("enabled", True):
            continue
        effect_type = str(effect.get("type", "punch_zoom"))
        intensity = max(0.0, min(float(effect.get("intensity", .05)), .35 if reduced else .65))
        enable = _beat_enable(effect, bpm, duration)
        output = f"vbeat{number}"
        if effect_type in {"twinkling_stars", "sparkle_burst"}:
            color = re.sub(r"[^a-zA-Z0-9#]", "", str(effect.get("color", "#FFFFFF"))) or "white"
            opacity = max(.04, min(float(effect.get("opacity", .32)), .38 if reduced else .8))
            seed = int(effect.get("seed", 1001) or 1001)
            rng = random.Random(seed)
            star_count = max(6, min(28, round(8 + intensity * 42)))
            base_size = max(12, round(min(width, height) * (.014 + intensity * .025)))
            for star_index in range(star_count):
                if effect_type == "sparkle_burst":
                    x_ratio = max(.05, min(.95, .5 + rng.uniform(-.34, .34)))
                    y_ratio = max(.08, min(.92, .5 + rng.uniform(-.30, .30)))
                else:
                    x_ratio = rng.uniform(.04, .96)
                    y_ratio = rng.uniform(.06, .94)
                size = max(10, round(base_size * rng.uniform(.58, 1.45)))
                phase = rng.uniform(0, 2.5)
                frequency = rng.uniform(1.7, 3.8)
                threshold = rng.uniform(-.4, .28)
                star_enable = f"({enable})*gt(sin((t+{phase:.4f})*{frequency:.4f}*2*PI),{threshold:.4f})"
                label = output if star_index == star_count - 1 else f"{output}s{star_index}"
                filters.append(
                    f"[{current_video}]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
                    f"text='✦':fontcolor={color}@{opacity:.3f}:fontsize={size}:"
                    f"x=(w-text_w)*{x_ratio:.5f}:y=(h-text_h)*{y_ratio:.5f}:enable='{star_enable}'[{label}]"
                )
                current_video = label
            continue
        if effect_type == "cinematic_bars":
            color = re.sub(r"[^a-zA-Z0-9#]", "", str(effect.get("color", "#000000"))) or "black"
            opacity = max(.05, min(float(effect.get("opacity", .3)), .45 if reduced else .8))
            bar_height = max(4, round(height * (.018 + intensity * .12)))
            middle = f"{output}top"
            filters.append(
                f"[{current_video}]drawbox=x=0:y=0:w=iw:h={bar_height}:color={color}@{opacity:.3f}:t=fill:enable='{enable}'[{middle}]"
            )
            filters.append(
                f"[{middle}]drawbox=x=0:y=ih-{bar_height}:w=iw:h={bar_height}:color={color}@{opacity:.3f}:t=fill:enable='{enable}'[{output}]"
            )
        elif effect_type in {"punch_zoom", "camera_shake", "radial_blur_pulse"}:
            zoom = 1 + intensity
            x = f"(iw-ow)/2+if({enable},sin(t*61)*{max(0, round(intensity * 18))},0)" if effect_type == "camera_shake" else "(iw-ow)/2"
            y = f"(ih-oh)/2+if({enable},cos(t*53)*{max(0, round(intensity * 14))},0)" if effect_type == "camera_shake" else "(ih-oh)/2"
            filters.append(
                f"[{current_video}]scale=w='ceil(iw*if({enable},{zoom:.5f},1)/2)*2':"
                f"h='ceil(ih*if({enable},{zoom:.5f},1)/2)*2':eval=frame,"
                f"crop={width}:{height}:x='{x}':y='{y}'[{output}]"
            )
        elif effect_type in {"glow_pulse", "light_leak_sweep", "animated_gradient", "vignette_pulse", "film_grain_accent", "chromatic_split"}:
            brightness = min(.14, intensity * .35)
            saturation = 1 + min(.35, intensity)
            filters.append(
                f"[{current_video}]eq=brightness={brightness:.4f}:saturation={saturation:.4f}:"
                f"enable='{enable}'[{output}]"
            )
        else:
            color = re.sub(r"[^a-zA-Z0-9#]", "", str(effect.get("color", "#7C6CF3"))) or "white"
            opacity = max(.02, min(float(effect.get("opacity", .18)), .35 if reduced else .65))
            filters.append(
                f"[{current_video}]drawbox=x=0:y=0:w=iw:h=ih:color={color}@{opacity:.3f}:t=fill:"
                f"enable='{enable}'[{output}]"
            )
        current_video = output
    return current_video


def _h264_level(fps: float) -> str:
    return "5.1" if fps > 60 else "4.2"


def _apply_decorative_frame(filters: list[str], current_video: str, template: dict) -> str:
    config = template.get("decorative_frame") or {}
    if not config.get("enabled"):
        return current_video
    style = str(config.get("style", "minimal"))
    color = re.sub(r"[^a-zA-Z0-9#]", "", str(config.get("color", "#FFFFFF"))) or "white"
    opacity = max(0.05, min(float(config.get("opacity", 0.9)), 1.0))
    thickness = max(2, min(int(config.get("thickness", 18)), 90))
    inset = max(0, min(int(config.get("inset", 24)), 180))
    stages: list[tuple[int, int, str, float]]
    if style == "double":
        stages = [
            (inset, thickness, color, opacity),
            (inset + thickness * 2, max(2, thickness // 3), color, opacity * 0.7),
        ]
    elif style == "neon":
        stages = [
            (inset, min(90, thickness * 3), color, opacity * 0.22),
            (inset + thickness, thickness, color, opacity),
        ]
    elif style == "cinematic":
        stages = [
            (inset, min(90, thickness + 16), "black", 0.8),
            (inset + max(4, thickness // 2), thickness, color, opacity),
        ]
    else:
        stages = [(inset, thickness, color, opacity)]

    for number, (stage_inset, stage_thickness, stage_color, stage_opacity) in enumerate(stages, start=1):
        safe_inset = min(stage_inset, 450)
        output = f"vframe{number}"
        filters.append(
            f"[{current_video}]drawbox=x={safe_inset}:y={safe_inset}:"
            f"w=iw-{safe_inset * 2}:h=ih-{safe_inset * 2}:"
            f"color={stage_color}@{max(0.02, min(stage_opacity, 1.0)):.3f}:t={stage_thickness}[{output}]"
        )
        current_video = output
    return current_video


async def render_main(
    source: Path,
    destination: Path,
    template: dict,
    template_root: Path,
    input_root: Path,
    progress: ProgressCallback,
    start: float = 4,
    span: float = 86,
    preview_seconds: int | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    info = await probe(source)
    output_profile = resolve_output_profile(template, info)
    output_width, output_height = int(output_profile["width"]), int(output_profile["height"])
    duration = min(info["duration"], preview_seconds) if preview_seconds else info["duration"]
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    input_index = 1
    overlays: list[tuple[int, dict]] = []
    for key in ("logo_overlay", "png_overlay", "watermark"):
        config = template.get(key) or {}
        asset = _safe_asset(config.get("path"), template_root, input_root) if config.get("enabled") else None
        if asset:
            command.extend(["-loop", "1", "-i", str(asset)])
            overlays.append((input_index, config))
            input_index += 1

    music_config = template.get("background_music") or {}
    music = _safe_asset(music_config.get("path"), template_root, input_root) if music_config.get("enabled") else None
    music_index: int | None = None
    if music:
        command.extend(["-stream_loop", "-1", "-i", str(music)])
        music_index = input_index
        input_index += 1

    beat_config = template.get("beat_track") or {}
    configured_stems = beat_config.get("stems") if isinstance(beat_config.get("stems"), dict) else {}
    beat_inputs: list[tuple[int, str]] = []
    if beat_config.get("enabled") and configured_stems and beat_config.get("use_stems", True):
        for stem_name, stem_value in configured_stems.items():
            stem_asset = _safe_asset(str(stem_value), template_root, input_root)
            if stem_asset:
                command.extend(["-stream_loop", "-1", "-i", str(stem_asset)])
                beat_inputs.append((input_index, stem_name))
                input_index += 1
    beat = _safe_asset(beat_config.get("asset"), template_root, input_root) if beat_config.get("enabled") and not beat_inputs else None
    beat_index: int | None = None
    if beat:
        command.extend(["-stream_loop", "-1", "-i", str(beat)])
        beat_index = input_index
        input_index += 1

    extra_audio_inputs: list[tuple[int, dict]] = []
    for track in template.get("audio_tracks") or []:
        asset = _safe_asset(track.get("path"), template_root, input_root)
        if track.get("path") and not asset:
            raise FFmpegError(f"Audio track asset is missing or outside allowed folders: {track.get('path')}")
        if not asset:
            continue
        if track.get("loop"):
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", str(asset)])
        extra_audio_inputs.append((input_index, track))
        input_index += 1

    filters = [f"[0:v]{_visual_chain(template, info)},format=yuv420p[v0]"]
    current_video = "v0"
    for number, (index, config) in enumerate(overlays, start=1):
        width = max(24, min(int(config.get("width", 220)), output_width))
        opacity = max(0.0, min(float(config.get("opacity", 1)), 1.0))
        x, y = _position(config.get("position", "top-right"), int(config.get("margin", 48)))
        visible_start, visible_end = _timing_bounds(config, duration)
        filters.append(f"[{index}:v]scale={width}:-1,format=rgba,colorchannelmixer=aa={opacity:.3f}[ov{number}]")
        filters.append(
            f"[{current_video}][ov{number}]overlay={x}:{y}:shortest=1:"
            f"enable='between(t,{visible_start:.3f},{visible_end:.3f})'[v{number}]"
        )
        current_video = f"v{number}"

    text_config = template.get("text") or {}
    text_value = str(text_config.get("value") or "").strip()
    if text_config.get("enabled") and text_value:
        font_size = max(16, min(int(text_config.get("font_size", 64)), 240))
        color = re.sub(r"[^a-zA-Z0-9#@.]", "", str(text_config.get("color", "white"))) or "white"
        box = ":box=1:boxcolor=black@0.45:boxborderw=24" if text_config.get("background", True) else ""
        visible_start, visible_end = _timing_bounds(text_config, duration)
        lines = _wrap_text(text_value, font_size).splitlines()
        positions = _text_line_positions(
            text_config.get("position", "bottom-center"), int(text_config.get("margin", 90)), font_size, len(lines)
        )
        for line_number, (line, (x, y)) in enumerate(zip(lines, positions), start=1):
            output_label = f"vtext{line_number}"
            filters.append(
                f"[{current_video}]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
                f"text='{_escape_filter_text(line)}':fontsize={font_size}:fontcolor={color}:x={x}:y={y}{box}:"
                f"enable='between(t,{visible_start:.3f},{visible_end:.3f})'[{output_label}]"
            )
            current_video = output_label

    for number, animated in enumerate(template.get("animated_elements") or [], start=1):
        animated_value = str(animated.get("text") or animated.get("value") or "").strip()
        if not animated.get("enabled", True) or not animated_value:
            continue
        visible_start, visible_end = _timing_bounds(animated, duration)
        font_size = max(16, min(int(animated.get("font_size", 64)), 240))
        color = re.sub(r"[^a-zA-Z0-9#@.]", "", str(animated.get("color", "white"))) or "white"
        box = ":box=1:boxcolor=black@0.52:boxborderw=26" if animated.get("background", True) else ""
        lines = _wrap_text(animated_value, font_size).splitlines()
        positions = _text_line_positions(
            animated.get("position", "bottom-center"), int(animated.get("margin", 90)), font_size, len(lines)
        )
        for line_number, (line, (base_x, base_y)) in enumerate(zip(lines, positions), start=1):
            x, y, alpha = _animated_drawtext_position(animated, visible_start, visible_end, base_x, base_y)
            output_label = f"vanimated{number}_{line_number}"
            filters.append(
                f"[{current_video}]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
                f"text='{_escape_filter_text(line)}':fontsize={font_size}:fontcolor={color}:x='{x}':y='{y}':alpha='{alpha}'{box}:"
                f"enable='between(t,{visible_start:.3f},{visible_end:.3f})'[{output_label}]"
            )
            current_video = output_label

    current_video = _apply_beat_effects(filters, current_video, template, duration, output_width, output_height)
    current_video = _apply_decorative_frame(filters, current_video, template)

    audio_mixer = template.get("audio_mixer") or {}
    muted = set(audio_mixer.get("muted") or [])
    soloed = set(audio_mixer.get("soloed") or [])

    def channel_volume(channel: str, configured: float) -> float:
        if channel in muted or (soloed and channel not in soloed):
            return 0.0
        return max(0.0, min(float(configured), 2.0))

    original_volume = channel_volume("original", template.get("original_audio_volume", 1.0))
    master_volume = max(0.0, min(float(template.get("master_audio_volume", 1.0)), 2.0))
    if info["has_audio"]:
        filters.append(f"[0:a]volume={original_volume:.3f},aresample=48000[aoriginal]")
    else:
        filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={max(duration, 0.1):.3f}[aoriginal]")
    audio_tracks = ["aoriginal"]
    if music_index is not None:
        music_volume = channel_volume("music", music_config.get("volume", 0.2))
        music_start, music_end = _timing_bounds(music_config, duration)
        delay_ms = round(music_start * 1000)
        music_length = max(0.04, music_end - music_start)
        fade_in = min(music_length / 2, max(0.0, float(music_config.get("fade_in", 1.0))))
        fade_out = min(music_length / 2, max(0.0, float(music_config.get("fade_out", 2.0))))
        fades = ""
        if fade_in:
            fades += f",afade=t=in:st=0:d={fade_in:.3f}"
        if fade_out:
            fades += f",afade=t=out:st={max(0.0, music_length - fade_out):.3f}:d={fade_out:.3f}"
        filters.append(
            f"[{music_index}:a]atrim=start=0:duration={music_length:.3f},asetpts=PTS-STARTPTS,"
            f"volume={music_volume:.3f}{fades},adelay={delay_ms}|{delay_ms},aresample=48000[amusic]"
        )
        audio_tracks.append("amusic")
    if beat_index is not None or beat_inputs:
        beat_volume = channel_volume("beat", beat_config.get("volume", .12))
        beat_start, beat_end = _timing_bounds(beat_config, duration)
        beat_delay = round(beat_start * 1000)
        beat_length = max(.04, beat_end - beat_start)
        if beat_inputs:
            stem_levels = beat_config.get("stem_levels") or {}
            stem_labels = []
            for number, (index, stem_name) in enumerate(beat_inputs, start=1):
                stem_level = max(0.0, min(float(stem_levels.get(stem_name, 1.0)), 2.0))
                label = f"abeatstem{number}"
                filters.append(f"[{index}:a]volume={stem_level:.3f},aresample=48000[{label}]")
                stem_labels.append(label)
            joined = "".join(f"[{label}]" for label in stem_labels)
            filters.append(f"{joined}amix=inputs={len(stem_labels)}:duration=longest:normalize=0[abeatbase]")
            beat_source = "abeatbase"
        else:
            beat_source = f"{beat_index}:a"
        filters.append(
            f"[{beat_source}]atrim=start=0:duration={beat_length:.3f},asetpts=PTS-STARTPTS,"
            f"volume={beat_volume:.3f},adelay={beat_delay}|{beat_delay},aresample=48000[abeatraw]"
        )
        ducking = beat_config.get("ducking") or {}
        if ducking.get("enabled") and info["has_audio"]:
            attack = max(1.0, min(float(ducking.get("attack_ms", 120)), 2000.0))
            release = max(10.0, min(float(ducking.get("release_ms", 450)), 9000.0))
            target = max(.01, min(float(ducking.get("speech_volume", .04)), 1.0))
            ratio = max(2.0, min(20.0, 1.0 / target))
            filters.append(
                f"[abeatraw][aoriginal]sidechaincompress=threshold=0.025:ratio={ratio:.3f}:"
                f"attack={attack:.1f}:release={release:.1f}:makeup=1[abeat]"
            )
        else:
            filters.append("[abeatraw]anull[abeat]")
        audio_tracks.append("abeat")

    extra_soloed = any(track.get("solo") for _, track in extra_audio_inputs)
    for number, (index, track) in enumerate(extra_audio_inputs, start=1):
        track_volume = 0.0 if track.get("muted") or (extra_soloed and not track.get("solo")) else max(0.0, min(float(track.get("volume", 1)), 2.0))
        track_start, track_end = _timing_bounds(track, duration)
        track_length = max(.04, track_end - track_start)
        delay_ms = round(track_start * 1000)
        fade_in = min(track_length / 2, max(0.0, float(track.get("fade_in", 0) or 0)))
        fade_out = min(track_length / 2, max(0.0, float(track.get("fade_out", 0) or 0)))
        fades = f",afade=t=in:st=0:d={fade_in:.3f}" if fade_in else ""
        if fade_out:
            fades += f",afade=t=out:st={max(0.0, track_length - fade_out):.3f}:d={fade_out:.3f}"
        label = f"aextra{number}"
        filters.append(
            f"[{index}:a]atrim=start=0:duration={track_length:.3f},asetpts=PTS-STARTPTS,"
            f"volume={track_volume:.3f}{fades},adelay={delay_ms}|{delay_ms},aresample=48000[{label}]"
        )
        audio_tracks.append(label)

    if len(audio_tracks) > 1:
        inputs = "".join(f"[{label}]" for label in audio_tracks)
        filters.append(f"{inputs}amix=inputs={len(audio_tracks)}:duration=first:dropout_transition=2[amixed]")
        audio_output = "amixed"
    else:
        audio_output = audio_tracks[0]
    final_filters = []
    if audio_mixer.get("normalize", True):
        final_filters.append("loudnorm=I=-14:LRA=11:TP=-1.5")
    final_filters.extend((f"volume={master_volume:.3f}", "alimiter=limit=0.95"))
    filters.append(f"[{audio_output}]{','.join(final_filters)}[aout]")
    audio_output = "aout"

    encoder = await _select_video_encoder()
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", f"[{current_video}]", "-map", f"[{audio_output}]",
        *_video_encoding_args(encoder, info["fps"]),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ])
    if preview_seconds:
        command.extend(["-t", str(preview_seconds)])
    command.extend(["-progress", "pipe:1", "-nostats", str(destination)])
    await _run_ffmpeg(command, duration, progress, start, span, cancel_event)


async def normalize_clip(
    source: Path,
    destination: Path,
    template: dict,
    volume: float = 1.0,
    target_frame_rate: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    info = await probe(source)
    frame_rate = target_frame_rate or info["frame_rate"]
    target_fps = info["fps"]
    if target_frame_rate:
        numerator, denominator = map(int, target_frame_rate.split("/", 1))
        target_fps = numerator / denominator
    filters = [f"[0:v]{_visual_chain(template, info)},fps={frame_rate},format=yuv420p[v]"]
    safe_volume = max(0.0, min(float(volume), 2.0))
    if info["has_audio"]:
        filters.append(f"[0:a]volume={safe_volume:.3f},aresample=48000[a]")
        audio_map = "[a]"
    else:
        filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={max(info['duration'], .1):.3f}[a]")
        audio_map = "[a]"
    encoder = await _select_video_encoder()
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", audio_map,
        *_video_encoding_args(encoder, target_fps),
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2", "-pix_fmt", "yuv420p", str(destination),
    ]
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await _communicate_with_cancellation(process, cancel_event)
    if process.returncode:
        raise FFmpegError(stderr.decode(errors="replace").strip() or "Could not normalize intro/outro")


async def render_timeline_source(
    source: Path,
    destination: Path,
    template: dict,
    template_root: Path,
    input_root: Path,
    cancel_event: asyncio.Event | None = None,
) -> Path:
    """Render an ordered project cut list into one normalized source for the main compositor."""
    clips = [clip for clip in (template.get("timeline") or {}).get("clips", []) if clip.get("enabled", True)]
    if not clips:
        return source
    source_info = await probe(source)
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    resolved: list[tuple[int, dict, dict]] = []
    for index, clip in enumerate(clips):
        clip_path = source
        if clip.get("path"):
            candidate = _safe_asset(str(clip["path"]), template_root, input_root)
            if not candidate:
                raise FFmpegError(f"Timeline clip asset is missing: {clip['path']}")
            clip_path = candidate
        info = await probe(clip_path)
        clip_start = max(0.0, float(clip.get("in", 0) or 0))
        default_end = info["duration"] if info["duration"] > 0 else clip_start + float(clip.get("duration", 3) or 3)
        clip_end = min(default_end, max(clip_start + .04, float(clip.get("out", default_end) or default_end)))
        if clip_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            clip_end = clip_start + max(.04, float(clip.get("duration", 3) or 3))
            command.extend(["-loop", "1", "-t", f"{clip_end - clip_start:.6f}", "-i", str(clip_path)])
        else:
            command.extend(["-i", str(clip_path)])
        resolved.append((index, {**clip, "in": clip_start, "out": clip_end}, info))

    filters: list[str] = []
    normalize_settings = {
        "output_profile": template.get("output_profile"),
        "scale": template.get("scale") or {"mode": "cover", "zoom": 1},
        "crop": template.get("crop") or {"focal_x": .5, "focal_y": .5},
    }
    for index, clip, info in resolved:
        start, end = clip["in"], clip["out"]
        filters.append(
            f"[{index}:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
            f"{_visual_chain(normalize_settings, info)},fps={source_info['frame_rate']},settb=AVTB,format=yuv420p[tv{index}]"
        )
        if info["has_audio"]:
            filters.append(
                f"[{index}:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ta{index}]"
            )
        else:
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={end - start:.6f},asetpts=PTS-STARTPTS[ta{index}]"
            )
    current_video, current_audio = "tv0", "ta0"
    timeline_duration = resolved[0][1]["out"] - resolved[0][1]["in"]
    for position in range(1, len(resolved)):
        previous_clip = resolved[position - 1][1]
        clip = resolved[position][1]
        clip_duration = clip["out"] - clip["in"]
        transition = str(previous_clip.get("transition", "cut"))
        next_video, next_audio = f"tv{position}", f"ta{position}"
        output_video, output_audio = f"tcv{position}", f"tca{position}"
        if transition in {"fade", "dissolve"}:
            transition_duration = max(.04, min(
                float(previous_clip.get("transition_duration", .3) or .3),
                timeline_duration / 2, clip_duration / 2, 1.5,
            ))
            offset = max(0.0, timeline_duration - transition_duration)
            filters.append(
                f"[{current_video}][{next_video}]xfade=transition={transition}:duration={transition_duration:.6f}:"
                f"offset={offset:.6f}[{output_video}]"
            )
            filters.append(
                f"[{current_audio}][{next_audio}]acrossfade=d={transition_duration:.6f}:c1=tri:c2=tri[{output_audio}]"
            )
            timeline_duration += clip_duration - transition_duration
        else:
            filters.append(
                f"[{current_video}][{current_audio}][{next_video}][{next_audio}]"
                f"concat=n=2:v=1:a=1[{output_video}][{output_audio}]"
            )
            timeline_duration += clip_duration
        current_video, current_audio = output_video, output_audio
    filters.append(f"[{current_video}]null[timelinev]")
    filters.append(f"[{current_audio}]anull[timelinea]")
    encoder = await _select_video_encoder()
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "[timelinev]", "-map", "[timelinea]",
        *_video_encoding_args(encoder, source_info["fps"]), "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2", "-pix_fmt", "yuv420p", str(destination),
    ])
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await _communicate_with_cancellation(process, cancel_event)
    if process.returncode:
        raise FFmpegError(stderr.decode(errors="replace").strip() or "Could not build the project cut list")
    return destination


async def process_video(
    source: Path,
    destination: Path,
    template: dict,
    template_root: Path,
    input_root: Path,
    progress: ProgressCallback,
    preview_seconds: int | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shorts-") as temp_name:
        temp = Path(temp_name)
        effective_source = source
        if (template.get("timeline") or {}).get("clips"):
            await progress(2, "building project cut list")
            effective_source = await render_timeline_source(
                source, temp / "timeline-source.mp4", template, template_root, input_root, cancel_event
            )
        if preview_seconds:
            await render_main(effective_source, destination, template, template_root, input_root, progress, 5, 93, preview_seconds, cancel_event)
            await progress(100, "completed")
            return

        intro_cfg = template.get("intro") or {}
        outro_cfg = template.get("outro") or {}
        intro = _safe_asset(intro_cfg.get("path"), template_root, input_root) if intro_cfg.get("enabled") else None
        outro = _safe_asset(outro_cfg.get("path"), template_root, input_root) if outro_cfg.get("enabled") else None
        joining_clips = [clip for clip in (intro, outro) if clip]
        source_info = await probe(effective_source) if joining_clips else None
        main_path = temp / "main.mp4" if joining_clips else destination
        await render_main(effective_source, main_path, template, template_root, input_root, progress, 4, 80 if joining_clips else 94, cancel_event=cancel_event)
        if not joining_clips:
            await progress(100, "completed")
            return

        ordered: list[Path] = []
        if intro:
            await progress(85, "preparing intro")
            normalized_intro = temp / "intro.mp4"
            await normalize_clip(intro, normalized_intro, template, intro_cfg.get("volume", 1.0), source_info["frame_rate"], cancel_event)
            ordered.append(normalized_intro)
        ordered.append(main_path)
        if outro:
            await progress(90, "preparing outro")
            normalized_outro = temp / "outro.mp4"
            await normalize_clip(outro, normalized_outro, template, outro_cfg.get("volume", 1.0), source_info["frame_rate"], cancel_event)
            ordered.append(normalized_outro)

        concat_file = temp / "concat.txt"
        concat_file.write_text("".join(f"file '{clip.as_posix()}'\n" for clip in ordered), encoding="utf-8")
        await progress(94, "joining clips")
        encoder = await _select_video_encoder()
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            *_video_encoding_args(encoder, source_info["fps"]),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
        ]
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await _communicate_with_cancellation(process, cancel_event)
        if process.returncode:
            raise FFmpegError(stderr.decode(errors="replace").strip() or "Could not join intro/outro")
        await progress(100, "completed")


def clean_partial(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
