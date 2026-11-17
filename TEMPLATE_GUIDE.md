# Video Template Creation Guide

This guide explains how to create reusable JSON presets for Local YouTube Remix Studio. Templates control framing, cropping, mirroring, text, animated callouts, image overlays, audio mixing, beat behavior, and intro/outro clips.

Final exports are MP4 files with H.264 video, AAC stereo audio, the source frame rate, and the selected output profile. A template without `output_profile` remains backward compatible and exports as 1080×1920 Shorts.

## Where templates and assets are stored

- Template files: `templates/*.json`
- Logos, overlays, music, intros, and outros: `templates/assets/`
- Recommended asset paths inside JSON: `assets/filename.ext`

Both directories are mounted into Docker, so files remain available after containers are rebuilt.

## Ways to create a template

### Using the editor

1. Open `http://localhost:3001`.
2. Click the **+** button in the Template editor.
3. Enter a preset name.
4. Configure the frame, text, animations, overlays, and audio.
5. Click **Save**.

### Importing JSON

1. Create a `.json` file using the format in this guide.
2. Click **Import JSON** in the Template editor.
3. Select the file.
4. Review its settings and click **Save** after making changes.

You can also copy a JSON file directly into `templates/` and refresh the browser.

## Minimum template

Only the template name is required:

```json
{
  "name": "My Template"
}
```

Missing sections use the application defaults. For reproducible results, production presets should explicitly include the settings they depend on.

## Complete template example

```json
{
  "name": "My Animated Short",

  "scale": {
    "mode": "cover",
    "zoom": 1.05
  },

  "crop": {
    "enabled": true,
    "focal_x": 0.5,
    "focal_y": 0.5
  },

  "mirror": {
    "horizontal": false,
    "vertical": false
  },

  "image_adjustments": {
    "enabled": false,
    "contrast": 1.0,
    "brightness": 0.0,
    "saturation": 1.0,
    "gamma": 1.0,
    "sharpness": 0.0
  },

  "decorative_frame": {
    "enabled": true,
    "style": "double",
    "color": "#FFD166",
    "thickness": 12,
    "inset": 24,
    "opacity": 0.9
  },

  "text": {
    "enabled": true,
    "value": "WATCH THIS TRANSFORMATION",
    "position": "top-center",
    "font_size": 72,
    "color": "#FFFFFF",
    "background": true,
    "margin": 100,
    "timing": {
      "start": 0,
      "end": 4
    }
  },

  "animated_elements": [
    {
      "id": "opening-callout",
      "enabled": true,
      "text": "HERE IS HOW IT STARTED",
      "animation": "slide-left",
      "position": "top-left",
      "font_size": 58,
      "color": "#FFD166",
      "background": true,
      "margin": 90,
      "timing": {
        "start": 5,
        "end": 10
      }
    },
    {
      "id": "result-callout",
      "enabled": true,
      "text": "THE FINAL RESULT",
      "animation": "fade",
      "position": "bottom-center",
      "font_size": 78,
      "color": "#FFFFFF",
      "background": true,
      "margin": 145,
      "timing": {
        "start": 50,
        "end": null
      }
    }
  ],

  "logo_overlay": {
    "enabled": false,
    "path": "assets/logo.png",
    "position": "top-right",
    "width": 180,
    "opacity": 1,
    "margin": 48,
    "timing": {
      "start": 0,
      "end": null
    }
  },

  "png_overlay": {
    "enabled": false,
    "path": "assets/overlay.png",
    "position": "center",
    "width": 1080,
    "opacity": 1,
    "margin": 0,
    "timing": {
      "start": 0,
      "end": null
    }
  },

  "watermark": {
    "enabled": false,
    "path": "assets/watermark.png",
    "position": "bottom-right",
    "width": 140,
    "opacity": 0.5,
    "margin": 48,
    "timing": {
      "start": 0,
      "end": null
    }
  },

  "background_music": {
    "enabled": true,
    "path": "assets/music.mp3",
    "volume": 0.18,
    "fade_in": 1.5,
    "fade_out": 2.5,
    "timing": {
      "start": 0,
      "end": null
    }
  },

  "original_audio_volume": 1,
  "master_audio_volume": 1,

  "intro": {
    "enabled": false,
    "path": "assets/intro.mp4",
    "volume": 1
  },

  "outro": {
    "enabled": false,
    "path": "assets/outro.mp4",
    "volume": 1
  }
}
```

## Parameter reference

### `name`

The name displayed in the preset selector.

- Required: yes
- Recommended maximum: 80 characters
- Preset names should be unique when importing or creating a new preset.

### `scale`

Controls how the source fills the vertical canvas.

| Parameter | Values | Description |
| --- | --- | --- |
| `mode` | `cover`, `contain` | `cover` fills and crops; `contain` preserves the full source with bars. |
| `zoom` | `1.0`–`3.0` | Additional zoom after fitting. The UI currently exposes `1.0`–`2.0`. |

Use `cover` for normal Shorts. Use `contain` when no source content may be cropped.

### `crop`

Chooses the visible area when `scale.mode` is `cover`.

| Parameter | Values | Description |
| --- | --- | --- |
| `enabled` | `true`, `false` | Records whether crop editing is enabled in the preset. |
| `focal_x` | `0.0`–`1.0` | Horizontal focus: `0` left, `0.5` center, `1` right. |
| `focal_y` | `0.0`–`1.0` | Vertical focus: `0` top, `0.5` center, `1` bottom. |

### `mirror`

Mirroring is applied before cropping and overlays.

| Parameter | Description |
| --- | --- |
| `horizontal` | Flips the image left-to-right. |
| `vertical` | Flips the image top-to-bottom. |

### `decorative_frame`

Adds a frame above all other visual layers.

| Parameter | Values | Description |
| --- | --- | --- |
| `enabled` | `true`, `false` | Shows or hides the frame. |
| `style` | `minimal`, `double`, `neon`, `cinematic` | Frame appearance. |
| `color` | Hex color | Example: `#FFD166`. |
| `thickness` | `2`–`80` recommended | Border thickness in output pixels. |
| `inset` | `0`–`160` recommended | Distance between the frame and video edge. |
| `opacity` | `0.1`–`1.0` | Frame transparency. |

### `image_adjustments`

Applies restrained FFmpeg image correction after the source has been fitted to the vertical canvas.

| Parameter | Values | Description |
| --- | --- | --- |
| `enabled` | `true`, `false` | Enables all image corrections in this section. |
| `contrast` | `0.5`–`2.0` | `1.0` is unchanged; values above `1.0` increase contrast. |
| `brightness` | `-0.5`–`0.5` | `0.0` is unchanged. |
| `saturation` | `0.0`–`3.0` | `1.0` is unchanged. |
| `gamma` | `0.5`–`2.0` | Midtone correction; `1.0` is unchanged. |
| `sharpness` | `0.0`–`1.5` | Adds local detail using FFmpeg's unsharp filter. Use moderate values to avoid halos. |

### `text`

Creates one static timed text layer.

Long titles wrap automatically using the same line-breaking rules in the live preview and FFmpeg export. New lines entered manually in the editor are preserved.

| Parameter | Description |
| --- | --- |
| `enabled` | Shows or hides the layer. |
| `value` | Text displayed in the video. |
| `position` | Placement on the 3×3 position grid. |
| `font_size` | Recommended range: `16`–`180`. |
| `color` | FFmpeg color name or hex value. |
| `background` | Adds a dark plate behind the text. |
| `margin` | Distance from the selected edge. |
| `timing` | When the text is visible. |

### `animated_elements`

An array containing any number of animated text callouts. Each item becomes an independent draggable layer in the timeline.

| Parameter | Values | Description |
| --- | --- | --- |
| `id` | Unique string | Keeps UI editing and timeline updates stable. |
| `enabled` | `true`, `false` | Shows or hides this callout. |
| `text` | String | Callout text. |
| `animation` | `fade`, `slide-left`, `slide-up`, `pulse` | Entry/display animation. |
| `position` | Position value | Placement on the video. |
| `font_size` | `16`–`240` | Output font size. |
| `color` | Color name or hex | Text color. |
| `background` | `true`, `false` | Dark text plate. |
| `margin` | Pixel value | Distance from the selected edge. |
| `timing` | Object | Start and end time in seconds. |

Use a different `id` for every animated element.

### Overlay sections

The following sections share the same basic format:

- `logo_overlay`
- `png_overlay`
- `watermark`

| Parameter | Description |
| --- | --- |
| `enabled` | Shows or hides the asset. |
| `path` | Asset path, normally `assets/file.png`. |
| `position` | Placement on the video. |
| `width` | Rendered width in output pixels, from `24` to `1080`. |
| `opacity` | `0.0` fully transparent to `1.0` fully visible. |
| `margin` | Distance from the selected edge. |
| `timing` | When the overlay is visible. |

Transparent PNG files are recommended for logos and graphic overlays.

### `background_music`

| Parameter | Description |
| --- | --- |
| `enabled` | Enables the music track. |
| `path` | Audio asset path. |
| `volume` | Music gain from `0.0` to `2.0`. |
| `fade_in` | Fade-in duration in seconds. |
| `fade_out` | Fade-out duration in seconds. |
| `timing` | Music placement on the main-video timeline. |

Music loops automatically when it is shorter than the requested duration.

Bundled local music choices:

- `assets/cinematic-build.mp3` — driving documentary music
- `assets/bunker-pulse.mp3` — tense, energetic construction pulse
- `assets/deep-focus.mp3` — calm, atmospheric background

These tracks can be selected and auditioned directly in the Audio editor. Custom MP3, WAV, M4A, and AAC files are also supported.

### Audio levels

| Parameter | Description |
| --- | --- |
| `original_audio_volume` | Gain for the source video's audio. |
| `master_audio_volume` | Gain applied after original audio and music are mixed. |
| `intro.volume` | Gain for the intro clip's audio. |
| `outro.volume` | Gain for the outro clip's audio. |

For all volume values:

- `0.0` = silent
- `0.5` = half gain
- `1.0` = unchanged
- `2.0` = double gain

A useful starting mix is original audio at `1.0`, music at `0.12`–`0.25`, and master output at `1.0`.

### `intro` and `outro`

Intro and outro videos are normalized to 1080×1920 H.264/AAC and joined before and after the processed main video.

| Parameter | Description |
| --- | --- |
| `enabled` | Adds the clip when true. |
| `path` | Video asset path. |
| `volume` | Clip audio gain. |

## Position values

Text and image overlays support these positions:

- `top-left`
- `top-center`
- `top-right`
- `center-left`
- `center`
- `center-right`
- `bottom-left`
- `bottom-center`
- `bottom-right`

For YouTube Shorts, avoid placing important text extremely close to the bottom or right edges because player controls may cover it.

## Timing format

Timing is measured in seconds on the main-video timeline:

```json
"timing": {
  "start": 4.5,
  "end": 9.75
}
```

Use `null` to keep a layer active until the end:

```json
"timing": {
  "start": 0,
  "end": null
}
```

Timing does not include the duration of an appended intro. The main video timeline always starts at zero.

## Asset recommendations

- Logos and overlays: transparent PNG files.
- Music: MP3, WAV, M4A, or AAC.
- Intro/outro: MP4 is recommended.
- Keep all assets inside `templates/assets/` or upload them through the editor.
- Use short filenames containing letters, numbers, hyphens, or underscores.
- Do not use remote URLs; processing is fully local.

## Recommended animated story structure

For a 45–60 second Short:

1. Hook at `0`–`4` seconds.
2. Context at `4`–`10` seconds.
3. Progress callout at `13`–`20` seconds.
4. Turning point at `28`–`38` seconds.
5. Final reveal at `48` seconds through the end.

Keep callouts short enough to read quickly. Four to eight words normally works best.

## Testing a template

1. Select a source video.
2. Select or import the template.
3. Save the template.
4. Use the player for an immediate visual approximation.
5. Click **Render 6s preview** to test the real FFmpeg output. The preview and final export automatically preserve the source video’s original frame rate.
6. Drag timeline clips to adjust timing, then save again.
7. Process one video before starting a large batch.

## Troubleshooting

### Template does not appear

- Confirm that the file ends in `.json`.
- Confirm that it is directly inside `templates/`.
- Validate the JSON syntax and refresh the browser.

### Asset is missing

- Confirm that `enabled` is `true`.
- Confirm that the asset exists inside `templates/assets/`.
- Use a relative path such as `assets/logo.png`.
- Save the preset after uploading or changing the path.

### Text or overlay appears at the wrong time

- Check `timing.start` and `timing.end`.
- Confirm that the end time is greater than the start time.
- Use the timeline to drag and resize the layer.

### Music is too loud

- Reduce `background_music.volume` first.
- Keep `original_audio_volume` near `1.0` when speech must remain clear.
- Use `master_audio_volume` for the final combined level.

### JSON fails to import

- JSON property names and text strings must use double quotes.
- Escape double quotes inside text with `\"`.
- Remove trailing commas after the last property or array item.

## Included reference preset

The most complete working reference is:

`templates/long-form-beat-remix.json`

Copy it, change its `name`, then customize the text, colors, timings, frame, overlays, and audio.

## Long-form output and beat fields

The reference long-form preset is `templates/long-form-beat-remix.json`. Older files remain valid: when `output_profile` is absent, the renderer uses `youtube-shorts` at 1080×1920.

| Field | Meaning |
| --- | --- |
| `output_profile.id` | `youtube-1080p`, `youtube-1440p`, `youtube-shorts`, or `preserve-source` |
| `output_profile.width`, `height` | Even output dimensions; built-in profiles supply these automatically |
| `output_profile.preserve_source_fps` | Keeps the probed source frame rate; currently always recommended |
| `beat_track.asset` | Generated mix path relative to `templates/`, such as `assets/beats/...wav` |
| `beat_track.bpm` | Grid tempo from analysis, manual entry, tap tempo, or beat metadata |
| `beat_track.offset` | Seconds from video zero to the first grid beat |
| `beat_track.stems` | Map of `kick`, `snare`, `percussion`, `bass`, and `atmosphere` WAV paths |
| `beat_track.stem_levels` | Per-stem linear gain; `0` is silent and `1` is unchanged |
| `beat_track.use_stems` | Mix stems when true; otherwise use the combined `asset` |
| `beat_track.ducking` | Side-chain settings that lower the beat while source dialogue is present |
| `beat_effects[]` | Deterministic effects triggered by beats, downbeats, every N beats, or timing |
| `motion_safety.reduced_flashing` | Caps effect intensity/opacity; defaults to true |
| `audio_mixer.normalize` | Targets -14 LUFS integrated loudness before peak limiting |
| `audio_mixer.muted`, `soloed` | Channel ID arrays using `original`, `music`, and `beat` |

Every beat effect accepts `id`, `type`, `enabled`, `trigger`, `every_n_beats`, `duration_ms`, `intensity`, `start`, `end`, `opacity`, `color`, `position`, `beat_offset`, `minimum_interval_ms`, and `seed`. Keep intensity below `0.1` for narration-led edits. `reduced_flashing` should remain enabled for general audiences.

Templates contain reusable styling and behavior only. Store source-specific analysis, clip decisions, transcript data, and asset selection in a project; see `PROJECT_JSON_GUIDE.md`.
