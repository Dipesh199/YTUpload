# Project JSON guide

Projects are saved under `templates/projects/` and are managed by the **Save project** controls. They are distinct from reusable templates: a template defines a look, while a project records one video's source identity, editor settings, timeline state, and cached beat grid.

```json
{
  "schema_version": 1,
  "name": "Episode 12 remix",
  "updated_at": 1790508000.0,
  "template_id": "long-form-beat-remix",
  "video_id": "0123456789abcdef",
  "settings": {
    "output_profile": {
      "id": "youtube-1080p",
      "width": 1920,
      "height": 1080,
      "aspect_ratio": "16:9",
      "preserve_source_fps": true
    },
    "beat_track": {
      "enabled": true,
      "asset": "assets/beats/neutral-creator-pulse/neutral-creator-pulse-81c2b5ba.wav",
      "bpm": 100,
      "offset": 0,
      "volume": 0.12,
      "loop": true
    },
    "audio_tracks": [
      {
        "id": "narration-pickup",
        "name": "Narration pickup",
        "type": "voiceover",
        "path": "assets/narration-pickup.wav",
        "volume": 1.0,
        "loop": false,
        "muted": false,
        "solo": false,
        "fade_in": 0.1,
        "fade_out": 0.15,
        "timing": { "start": 12.4, "end": 18.1 }
      }
    ]
  },
  "beat_analysis": {
    "bpm": 99.384,
    "offset": 0.1161,
    "beats": [0.1161, 0.7198],
    "downbeats": [0.1161],
    "waveform": [0.1, 0.4, 0.8]
  },
  "timeline": {
    "current_time": 0,
    "clips": [
      {
        "id": "opening",
        "name": "Source opening",
        "path": "",
        "in": 0.0,
        "out": 8.5,
        "enabled": true,
        "transition": "fade",
        "transition_duration": 0.3
      },
      {
        "id": "b-roll-1",
        "name": "Workshop B-roll",
        "path": "workshop-b-roll.mp4",
        "in": 2.0,
        "out": 6.0,
        "enabled": true,
        "transition": "cut"
      },
      {
        "id": "still-1",
        "name": "Plan diagram",
        "path": "assets/plan.png",
        "kind": "image",
        "in": 0,
        "out": 3,
        "duration": 3,
        "enabled": true,
        "transition": "dissolve",
        "transition_duration": 0.25
      }
    ],
    "tracks": []
  }
}
```

`video_id` is a stable hash of the source path relative to `input/`; if the source is moved or renamed, reopen the new media item and save the project again. `beat_analysis` is embedded so reopening a project restores markers immediately. The backend also caches analysis under `templates/assets/analysis/`, keyed by path, file size, and modification time.

Project writes use a temporary JSON file followed by an atomic rename. Names are sanitized before becoming filenames, and project/asset paths are constrained to their mounted roots to prevent directory traversal.

`timeline.clips` is the ordered render cut list. An empty `path` means the project's selected source; other paths are constrained to `input/` or `templates/`. `in`/`out` trim video assets. Still images use `duration`. `transition` accepts `cut`, `fade`, or `dissolve`; `transition_duration` is capped to a safe value based on both neighboring clips. The editor provides split, delete, reorder, beat snapping, and a 40-state undo/redo history.

`settings.audio_tracks` stores independent `voiceover`, `music`, or `sfx` lanes. Each supports asset path, volume, loop, mute, solo, fades, and `timing.start`/`timing.end`. Audio clips are draggable on the timeline and enter the same FFmpeg master mix, normalization, and peak limiter as source audio, background music, and generated beat stems.

`timeline.tracks` remains available for forward-compatible track grouping. Unknown fields are preserved and can store transcript, scene-marker, proxy, or render-cache metadata.

Do not place reusable logos, colors, or effect defaults only in a project; keep those in a template. Do not place source-specific timestamps or analysis in a template.
