from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

import librosa
import numpy as np
import soundfile as sf

ProgressCallback = Callable[[float, str], Awaitable[None]]
SAMPLE_RATE = 48_000


class BeatGenerationCancelled(RuntimeError):
    pass

DEFAULT_BEATS = (
    {"slug": "neutral-creator-pulse", "name": "Neutral Creator Pulse", "bpm": 100, "mood": "neutral creator", "seed": 1001},
    {"slug": "energetic-pop-motion", "name": "Energetic Pop Motion", "bpm": 120, "mood": "energetic pop", "seed": 1202},
    {"slug": "cinematic-drive", "name": "Cinematic Drive", "bpm": 96, "mood": "cinematic", "seed": 9603},
    {"slug": "hip-hop-bounce", "name": "Hip-Hop Bounce", "bpm": 90, "mood": "hip-hop", "seed": 9004},
    {"slug": "electronic-momentum", "name": "Electronic Momentum", "bpm": 128, "mood": "electronic", "seed": 12805},
)


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower().strip()).strip("-") or "generated-beat"


def _envelope(length: int, attack: float = 0.02, decay: float = 8.0) -> np.ndarray:
    x = np.linspace(0, 1, length, endpoint=False)
    return np.minimum(1.0, x / max(attack, 1e-4)) * np.exp(-decay * x)


def _place(target: np.ndarray, sound: np.ndarray, start: int) -> None:
    if start >= len(target):
        return
    count = min(len(sound), len(target) - start)
    target[start:start + count] += sound[:count]


def _tone(frequency: float, seconds: float, amplitude: float, decay: float, phase: float = 0.0) -> np.ndarray:
    length = max(1, round(seconds * SAMPLE_RATE))
    t = np.arange(length, dtype=np.float64) / SAMPLE_RATE
    return amplitude * np.sin(2 * np.pi * frequency * t + phase) * _envelope(length, decay=decay)


def _kick(seconds: float, amplitude: float) -> np.ndarray:
    length = max(1, round(seconds * SAMPLE_RATE))
    t = np.arange(length, dtype=np.float64) / SAMPLE_RATE
    phase = 2 * np.pi * (44 * t + 55 * (1 - np.exp(-22 * t)) / 22)
    return amplitude * np.sin(phase) * _envelope(length, attack=.006, decay=10)


def _noise_hit(rng: np.random.Generator, seconds: float, amplitude: float, decay: float, highpass: bool = False) -> np.ndarray:
    length = max(1, round(seconds * SAMPLE_RATE))
    noise = rng.normal(0, 1, length)
    if highpass and length > 1:
        noise = np.concatenate(([0.0], np.diff(noise)))
    return amplitude * noise * _envelope(length, attack=.004, decay=decay)


def _pan_mono(mono: np.ndarray, pan: float = 0.0) -> np.ndarray:
    angle = (max(-1.0, min(1.0, pan)) + 1) * math.pi / 4
    return np.column_stack((mono * math.cos(angle), mono * math.sin(angle)))


def generate_beat(
    beat_root: Path,
    *,
    name: str,
    bpm: float,
    bars: int = 8,
    intensity: float = .55,
    swing: float = 0.0,
    density: float = .55,
    bass_level: float = .55,
    percussion_level: float = .65,
    seed: int = 1001,
    slug: str | None = None,
    cancel_event: object | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    """Generate a deterministic, sample-free loop and separate 48 kHz stereo stems."""
    bpm = float(np.clip(bpm, 60, 180))
    bars = int(np.clip(bars, 1, 32))
    intensity = float(np.clip(intensity, 0, 1))
    swing = float(np.clip(swing, 0, .45))
    density = float(np.clip(density, 0, 1))
    bass_level = float(np.clip(bass_level, 0, 1))
    percussion_level = float(np.clip(percussion_level, 0, 1))
    slug = safe_slug(slug or name)
    settings = {
        "bpm": bpm, "bars": bars, "intensity": intensity, "swing": swing,
        "density": density, "bass_level": bass_level, "percussion_level": percussion_level, "seed": int(seed),
    }
    signature = hashlib.sha1(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:8]
    def checkpoint(percent: float, message: str) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise BeatGenerationCancelled("Beat generation cancelled")
        if progress:
            progress(percent, message)

    checkpoint(5, "Preparing deterministic synthesis")
    beat_root.mkdir(parents=True, exist_ok=True)
    duration = bars * 4 * 60 / bpm
    total = round(duration * SAMPLE_RATE)
    seconds_per_beat = 60 / bpm
    rng = np.random.default_rng(int(seed))
    stems = {key: np.zeros((total, 2), dtype=np.float64) for key in ("kick", "snare", "percussion", "bass", "atmosphere")}

    root_notes = [43.65, 49.0, 55.0, 65.41]
    root = root_notes[int(seed) % len(root_notes)]
    for beat in range(bars * 4):
        if beat % 4 == 0:
            checkpoint(10 + 58 * beat / max(1, bars * 4), "Synthesizing rhythm and bass")
        beat_time = beat * seconds_per_beat
        accent = 1.12 if beat % 4 == 0 else 1.0
        _place(stems["kick"], _pan_mono(_kick(.32, (.42 + .28 * intensity) * accent)), round(beat_time * SAMPLE_RATE))
        if beat % 4 in (1, 3):
            snare = _noise_hit(rng, .24, (.12 + .2 * intensity) * percussion_level, 12)
            snare += _tone(175, .24, .10 * percussion_level, 14)
            _place(stems["snare"], _pan_mono(snare), round(beat_time * SAMPLE_RATE))
        bass_frequency = root * (1 if beat % 8 not in (6, 7) else 1.5)
        bass = _tone(bass_frequency, seconds_per_beat * .82, .26 * bass_level, 3.8)
        bass += .14 * _tone(bass_frequency * 2, seconds_per_beat * .82, bass_level, 5.5)
        _place(stems["bass"], _pan_mono(bass, -.08), round(beat_time * SAMPLE_RATE))

        subdivisions = 4 if density > .7 else 2
        for subdivision in range(subdivisions):
            step = beat_time + subdivision * seconds_per_beat / subdivisions
            if subdivision % 2 and swing:
                step += seconds_per_beat / subdivisions * swing
            if rng.random() < .58 + .4 * density:
                hat = _noise_hit(rng, .055, (.045 + .05 * intensity) * percussion_level, 28, highpass=True)
                _place(stems["percussion"], _pan_mono(hat, (-.3 if subdivision % 2 else .3)), round(step * SAMPLE_RATE))

    # A sparse, non-melodic two-note atmosphere; all synthesis is generated here.
    bar_seconds = seconds_per_beat * 4
    for bar in range(bars):
        checkpoint(68 + 8 * bar / max(1, bars), "Synthesizing atmospheric stem")
        start = round(bar * bar_seconds * SAMPLE_RATE)
        note = root * (2 if bar % 4 < 2 else 2.25)
        atmosphere = _tone(note, bar_seconds * .96, .035 + .035 * intensity, .9, phase=math.pi / 4)
        atmosphere += _tone(note * 1.5, bar_seconds * .96, .018 + .018 * intensity, .75)
        _place(stems["atmosphere"], _pan_mono(atmosphere, .2 if bar % 2 else -.2), start)

    peak = max(float(np.max(np.abs(stem))) for stem in stems.values()) or 1.0
    mix = sum(stems.values())
    mix_peak = float(np.max(np.abs(mix))) or 1.0
    gain = min(.92 / mix_peak, .92 / peak)
    checkpoint(78, "Writing 48 kHz stereo stems")
    with tempfile.TemporaryDirectory(prefix=".beat-generation-", dir=beat_root) as temp_name:
        working_folder = Path(temp_name) / slug
        working_folder.mkdir(parents=True, exist_ok=True)
        paths: dict[str, str] = {}
        for index, (stem_name, samples) in enumerate(stems.items()):
            checkpoint(80 + index * 2.5, f"Writing {stem_name} stem")
            stem_path = working_folder / f"{slug}-{stem_name}-{signature}.wav"
            sf.write(stem_path, np.clip(samples * gain, -.98, .98).astype(np.float32), SAMPLE_RATE, subtype="PCM_16")
            paths[stem_name] = f"assets/beats/{slug}/{stem_path.name}"
        checkpoint(93, "Writing seamless master loop")
        mix_path = working_folder / f"{slug}-{signature}.wav"
        sf.write(mix_path, np.clip(mix * gain, -.98, .98).astype(np.float32), SAMPLE_RATE, subtype="PCM_16")
        metadata = {
            "id": slug, "name": name.strip() or slug.replace("-", " ").title(), "asset": f"assets/beats/{slug}/{mix_path.name}",
            "bpm": bpm, "time_signature": "4/4", "bar_count": bars, "mood": name.strip(),
            "duration": duration, "sample_rate": SAMPLE_RATE, "channels": 2, "seamless": True,
            "ownership": "Locally generated original audio; no third-party samples", "license": "Original user-owned project asset",
            "seed": int(seed), "settings": settings, "stems": paths,
        }
        (working_folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        checkpoint(97, "Publishing beat to the persistent library")
        folder = beat_root / slug
        folder.mkdir(parents=True, exist_ok=True)
        for generated_path in working_folder.iterdir():
            generated_path.replace(folder / generated_path.name)
        checkpoint(100, "Beat generation complete")
    return metadata


def ensure_default_beats(beat_root: Path) -> None:
    for item in DEFAULT_BEATS:
        if not (beat_root / item["slug"] / "metadata.json").exists():
            generate_beat(beat_root, name=item["name"], bpm=item["bpm"], seed=item["seed"], slug=item["slug"])


def list_beats(beat_root: Path) -> list[dict]:
    results = []
    for path in sorted(beat_root.glob("*/metadata.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            asset = beat_root.parents[1] / data.get("asset", "")
            if asset.exists():
                results.append(data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return results


async def analyze_audio(
    source: Path,
    cache_path: Path,
    progress: ProgressCallback,
    cancel_event: asyncio.Event | None = None,
) -> dict:
    if cache_path.exists():
        await progress(100, "Loaded cached analysis")
        return json.loads(cache_path.read_text(encoding="utf-8"))
    await progress(8, "Decoding audio")
    if cancel_event and cancel_event.is_set():
        raise asyncio.CancelledError

    with tempfile.TemporaryDirectory(prefix="beat-analysis-") as temp_name:
        decoded = Path(temp_name) / "audio.wav"
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(decoded),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        communicate_task = asyncio.create_task(process.communicate())
        cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event else None
        wait_for = {communicate_task, cancel_task} if cancel_task else {communicate_task}
        done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task and cancel_task in done and cancel_event and cancel_event.is_set():
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            communicate_task.cancel()
            await asyncio.gather(communicate_task, return_exceptions=True)
            raise asyncio.CancelledError
        _, stderr = await communicate_task
        if cancel_task:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
        if process.returncode or not decoded.exists():
            raise ValueError(stderr.decode(errors="replace").strip() or "FFmpeg could not decode this audio")
        await progress(32, "Detecting tempo and beats")

        def analyze() -> dict:
            y, sr = librosa.load(decoded, sr=22_050, mono=True)
            if not y.size:
                raise ValueError("No analyzable audio was found")
            onset = librosa.onset.onset_strength(y=y, sr=sr)
            tempo_value, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, units="frames")
            bpm = float(np.asarray(tempo_value).reshape(-1)[0]) if np.asarray(tempo_value).size else 0.0
            beat_times = librosa.frames_to_time(beat_frames, sr=sr).astype(float)
            if bpm <= 0 or not len(beat_times):
                raise ValueError("A reliable beat grid could not be detected; use manual BPM and offset")
            strengths = onset[np.clip(beat_frames, 0, max(0, len(onset) - 1))]
            phase_scores = [float(np.sum(strengths[index::4])) for index in range(4)]
            downbeat_phase = int(np.argmax(phase_scores))
            downbeats = beat_times[downbeat_phase::4]
            window = max(1, math.ceil(len(y) / 512))
            waveform = [float(np.max(np.abs(y[index:index + window]))) for index in range(0, len(y), window)][:512]
            waveform_peak = max(waveform) or 1.0
            return {
                "bpm": round(bpm, 3), "offset": round(float(beat_times[0]), 6),
                "beats": [round(float(value), 6) for value in beat_times],
                "downbeats": [round(float(value), 6) for value in downbeats],
                "duration": round(len(y) / sr, 6), "waveform": [round(value / waveform_peak, 4) for value in waveform],
                "algorithm": "librosa dynamic-programming beat tracker", "sample_rate": sr,
            }

        result = await asyncio.to_thread(analyze)
    if cancel_event and cancel_event.is_set():
        raise asyncio.CancelledError
    await progress(92, "Saving beat grid")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    await progress(100, "Beat analysis complete")
    return result
