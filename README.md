# Local YouTube Remix Studio

A private, single-administrator YouTube automation dashboard and local video editor for landscape videos and 1080×1920 Shorts. The automation dashboard is the main interface; the complete editor remains available as its render engine and advanced workspace. React, FastAPI, PostgreSQL, and FFmpeg run locally. Existing Shorts presets remain backward compatible.

Read [`CURRENT_CAPABILITIES.md`](CURRENT_CAPABILITIES.md) for the audited feature set that exists today. Read [`MULTI_CHANNEL_AUTOMATION_BLUEPRINT.md`](MULTI_CHANNEL_AUTOMATION_BLUEPRINT.md) before implementing the destination-first URL workflow, multi-channel dashboard, continuous pipelines, uploads, scheduling, or cleanup behavior.

## What it does

- Opens on a single-administrator automation dashboard with Overview, Channels, New URL Batch, Work queue, and History pages.
- Registers destination channel identities and securely OAuth-connects the selected YouTube channel, with explicit confirmation before adopting a different Brand Account or channel identity.
- Requires a destination channel before accepting a URL batch.
- Adds durable source-channel imports: choose a destination, paste a YouTube channel or `@handle` URL, and queue every newly discovered public video for alteration. Reconciliation skips source/destination pairs already recorded.
- Parses newline-, comma-, or space-separated YouTube links; normalizes watch, short, Shorts, live, and embed URLs; and detects invalid or duplicate entries.
- Saves immutable batch settings and durable source/render/publish/upload-session records in PostgreSQL.
- Automatically downloads every accepted batch item, applies its frozen remix preset/output profile, validates the altered export, and provides it from **Work queue**.
- Shows explicit batch, item, render, and publish percentages in **Work queue**. A completed source can be opened directly in the advanced editor; **Save video** creates a new immutable pipeline version and safely replaces that item's altered export after a validated re-render.
- Shows the persisted YouTube public-release date, local time, and timezone on every scheduled video row in **Work queue**.
- Defaults dashboard batches to scheduled publication, assigns public release slots 30 minutes apart, and applies frozen playlist, thumbnail, notification, source-cleanup, and export-retention choices during publishing.
- Runs restart-safe resumable YouTube uploads, persists encrypted sessions and acknowledged bytes, polls YouTube processing, and supports manual retry without duplicate video creation.
- Stores a 15-minute fallback cadence for immediate public uploads. Scheduled jobs bypass that delay, while Overview shows their YouTube release slot and countdown.
- Hands each completed render to the publisher immediately instead of waiting for its batch. Scheduled jobs upload consecutively as soon as the publisher is free; their public release times remain 30 minutes apart on YouTube. Immediate public jobs retain the destination upload cadence.
- Lets the operator pause or resume new alteration work for a batch without stopping uploads of videos that are already rendered. Scheduled uploads are sent privately with `publishAt`, so YouTube can make them public after the local system is turned off.
- Existing immediate-public batches expose **Schedule remaining**, which converts only videos that have not begun uploading and keeps already uploaded videos unchanged.
- Limits new visibility choices to **Scheduled public** and **Public immediately**. A channel operation verifies existing unscheduled uploads and makes any private/unlisted videos public without cancelling scheduled releases.
- Preserves queue and event history across backend restarts.
- Upload multiple MP4 files, choose a browser folder, copy MP4s into `input/`, or download an authorized YouTube video directly into Media.
- Track YouTube download percentage, transfer speed, ETA, completion, errors, cancellation, and one-click retry through the `yt-dlp` integration. Direct HTTPS H.264/M4A streams are preferred for reliable browser-compatible MP4 imports.
- Detect and list videos recursively from the mounted input folder.
- Create, edit, save, and delete JSON template presets.
- Import compatible `.json` presets directly from the template editor.
- Add logo, PNG, and watermark overlays; positioned text; crop/focus, zoom, horizontal/vertical mirroring, decorative frames, background music, intro, and outro clips.
- Apply optional contrast, brightness, saturation, gamma, and detail-sharpening corrections from JSON presets.
- Build multiple timed animated callouts with fade, slide-left, slide-up, or pulse motion. Every callout has its own timeline clip, position, type styling, and timing.
- Mix original sound, background music, intro/outro audio, and master output with independent levels and music fades.
- Preserve each source video’s original frame rate without generating intermediate frames, reducing export time and file size.
- Render a real six-second FFmpeg preview for the selected video and saved preset.
- Split, trim, delete, and reorder the source cut list; append B-roll or still images; configure cut/fade/dissolve transitions; and undo or redo clip edits.
- Scrub a zoomable multi-track timeline with a cached waveform and beat/downbeat markers. Drag or trim overlays, generated beats, voiceover, uploaded music, and sound-effect lanes with beat-grid snapping.
- Process selected videos or the whole input folder sequentially.
- Delete individual input or output videos from the Media and Exports tabs, or delete multiple selected input videos together.
- Cancel an individual queued or active job from Media or Active Exports, or cancel the entire active batch; FFmpeg is stopped and unfinished renders are removed.
- Track queued, processing, completed, and failed state per video. A failed item is recorded and the next item continues.
- Review every finished file in the **Exports** tab, including its exact filename, source, preset, duration, size, timestamp, and download link.
- Every export gets a collision-resistant prefix such as `EXP-20260912-143210-A3F9C2_`, so exporting the same source and preset again never overwrites an earlier result.
- Export MP4 files with H.264 video, AAC stereo audio, selected 1080p/1440p/Shorts/source geometry, source frame rate, and web fast-start metadata.

## Long-form remix workflow

- Choose **YouTube Landscape** (1920×1080), **YouTube 1440p** (2560×1440), **YouTube Shorts** (1080×1920), or **Preserve Source**. `cover` crops without stretching; `contain` adds bars.
- Start from **Long-Form Beat Remix**, which keeps dialogue clear, uses a restrained downbeat punch/glow treatment, does not mirror footage, and preserves narrative order.
- Click **Analyze source audio** to extract PCM locally with FFmpeg and detect BPM, beat/downbeat timestamps, and a cached waveform with librosa. Correct BPM/offset manually or use **Tap tempo** when source audio has no reliable rhythm.
- Choose one of five locally synthesized original loops or generate a seeded variation. Kick, snare, percussion, bass, and atmosphere are stored as independent 48 kHz stereo stems and can be mixed separately. Generation runs as a cancellable progress job and publishes files only after all stems are complete.
- Beat effects snap to the shared beat grid and use the same BPM/offset fields in preview and FFmpeg output. Reduced-flashing safety is enabled by default.
- Save video-specific state with **Save project**. Projects live separately from reusable templates so source IDs, analysis, timeline data, and edits do not leak into presets.

See [PROJECT_JSON_GUIDE.md](PROJECT_JSON_GUIDE.md) and [BEAT_TEMPLATE_GUIDE.md](BEAT_TEMPLATE_GUIDE.md) for the complete schemas.

## Requirements

- Docker Desktop (or Docker Engine with Docker Compose v2)
- A modern browser
- Enough free disk space for source, preview, temporary, and output video files

No local Python, Node.js, or FFmpeg installation is required. FFmpeg and the DejaVu text font are installed inside the backend image.

## Start the app

1. Open a terminal in this project folder.
2. Optional: copy `.env.example` to `.env` and adjust ports or encoding settings.
3. Build and start the PostgreSQL, backend, and frontend containers:

   ```bash
   docker compose up --build
   ```

4. Open [http://localhost:3001](http://localhost:3001) with the included `.env` configuration.

To import from YouTube, open **Media**, click **Download from YouTube**, paste a video URL, and start the download. Only download videos you own or have permission to use. Playlists are limited to one video per request. Completed MP4 files appear automatically in Media and remain in the mounted `input/` folder. The source title, description, tags, category names, channel details, and URL are saved in a small `.metadata.json` sidecar beside the MP4. That metadata is returned with Media and Exports API records and is preserved when the source is rendered, ready to prefill a later YouTube upload.

The FastAPI documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

Stop the system with:

```bash
docker compose down
```

Add `-d` to the startup command to run it in the background. Use `docker compose logs -f backend` to inspect detailed encoding errors.

## Automation dashboard

The app opens on the automation dashboard. Use **Advanced editor** in the sidebar to open the existing media, template, timeline, beat, preview, and export workspace.

The durable automation path includes real channel authorization and scheduled-public publishing that is private until YouTube's release time:

1. Register the stable ID of a destination YouTube channel. This creates a paused, `not_connected` local record.
2. Open **Channels**, click **Connect with Google**, and choose a YouTube channel. The backend validates one-time OAuth state and verifies the returned channel ID. Exact matches connect immediately. If Google authorizes a different channel or Brand Account, Channels shows its actual title and ID and requires **Use this authorized YouTube channel for this destination** before updating the stable external ID and activating encrypted offline credentials. Connected destinations expose **Disconnect Google**; disconnected destinations can be safely removed from active Channels without deleting their existing batches or history.
3. Open **New URL Batch** and select that destination before entering links.
4. Validate the URL list. The preview identifies accepted, repeated, previously recorded, and invalid rows.
5. Choose the remix preset, output format, metadata mode, visibility/schedule, playlist IDs, thumbnail behavior, subscriber notification, source-cleanup rule, and export retention.
6. Create the immutable batch. Accepted rows reserve durable render and publish jobs and an upload-session record.
7. The worker downloads each accepted source, preserves its metadata, applies the frozen preset through FFmpeg, validates the selected output geometry, and records the altered export.
8. Review render, upload, and YouTube-processing progress in **Work queue**. **Pause processing** finishes the current alteration and prevents the next one from starting; completed videos continue uploading. **Resume processing** continues the remaining items. Use **Edit video** before source cleanup, or **Save video** to queue edited settings and timeline as the item's next altered export. Failed items can be retried independently; append-only transitions appear in **History**.
9. The publisher verifies the exact destination, uploads resumable chunks, applies frozen metadata and post-upload actions, and marks scheduled uploads complete only after YouTube reports processing success. YouTube receives scheduled videos as private and publishes them automatically at their `publishAt` times.

For a full channel import, open **Source channels**, choose the destination, paste a YouTube channel or `@handle` URL, choose the remix and output format, and click **Import all videos**. Discovery merges the channel's Videos, Shorts, and Streams tabs without downloading media. New items enter the same durable queue; **Sync new videos** repeats discovery and skips existing source/destination pairs.

Disconnected destinations remain `blocked_not_connected`. Connected exports progress through `waiting_for_upload`, `uploading`, `youtube_processing`, and `scheduled` or `published`. Resumable session URLs and OAuth tokens are encrypted at rest; configured source cleanup never runs before confirmed YouTube processing.

## Persistent folders

| Host folder | Container folder | Purpose |
| --- | --- | --- |
| `input/` | `/app/input` | Source MP4 files |
| `output/` | `/app/output` | Finished long-form and Shorts exports |
| `templates/` | `/app/templates` | JSON presets |
| `templates/assets/` | `/app/templates/assets` | Logos, overlays, music, intros, and outros |
| `templates/projects/` | `/app/templates/projects` | Saved video-specific projects |
| `templates/assets/beats/` | `/app/templates/assets/beats` | Generated loops, stems, and beat metadata |
| `templates/assets/analysis/` | `/app/templates/assets/analysis` | Cached beat grids and waveforms |
| Docker volume `postgres-data` | `/var/lib/postgresql` | Durable automation channels, batches, jobs, sessions, and events |

Docker mounts these directories, so their files remain after containers are rebuilt or removed. Rendered six-second previews use a Docker volume because they are disposable cache files.

## Typical workflow

1. Add MP4 files using **Upload MP4s**, **Choose folder**, or by copying them into `input/`. Click refresh if you copied files while the app was open.
2. Select the included **Clean Short** preset, or click **+** to create one.
3. Adjust crop, text, animated callouts, overlays, audio, intro, and outro. Asset upload buttons copy chosen files into `templates/assets/`.
4. Drag enabled layers in the timeline to choose when they appear. Drag either edge to trim the layer, then save the preset. Processing always uses the saved JSON, which makes batch results reproducible.
5. Select a video and click **Render 6s preview** to verify the actual FFmpeg result.
6. Select individual videos and click **Process selected**, or click **Process all**.
7. When the batch finishes, the UI opens the **Exports** tab automatically. Finished files also appear in `output/`.

### Import a preset JSON file

Click **Import JSON** in the Template editor and choose one `.json` file. The app accepts either the flat format used by files in `templates/` or an object containing `name` and a nested `settings` object. It validates the JSON, saves it as a new persistent preset, selects it, and makes it immediately available for preview and processing.

Preset names must be unique. If an imported name already exists, change the `name` value in the JSON or rename the existing preset first.

## Template format

Each top-level JSON file in `templates/` is a preset. See [`templates/bunker-build-documentary.json`](templates/bunker-build-documentary.json) for every supported option. Asset paths should normally be relative to the templates folder, for example `assets/logo.png`. Paths are restricted to the mounted `templates/` and `input/` directories.

For a complete creation walkthrough, copy-ready JSON, parameter tables, timing guidance, and troubleshooting, see [`TEMPLATE_GUIDE.md`](TEMPLATE_GUIDE.md).

Templates without `output_profile` intentionally render as legacy 1080×1920 Shorts. New templates should specify a profile. The included [`templates/long-form-beat-remix.json`](templates/long-form-beat-remix.json) is the reference long-form preset.

Important values:

- `scale.mode`: `cover` fills the vertical frame and crops overflow; `contain` preserves the full image with black bars.
- `scale.zoom`: a value from `1.0` to `3.0`.
- `crop.focal_x` and `crop.focal_y`: values from `0.0` to `1.0` that choose which part of an oversized frame remains visible. `0.5` centers the crop.
- `mirror.horizontal` and `mirror.vertical`: flip the source before cropping and layering.
- `decorative_frame`: adds a `minimal`, `double`, `neon`, or `cinematic` frame with editable color, thickness, inset, and opacity.
- Frame rate is detected from each source automatically and preserved in previews and final exports. It is not configured in template JSON.
- Overlay and text positions: `top-left`, `top-center`, `top-right`, `center-left`, `center`, `center-right`, `bottom-left`, `bottom-center`, or `bottom-right`.
- `timing.start` and `timing.end`: seconds on the main-video timeline. A `null` end value means the layer remains active to the end.
- `animated_elements`: an array of independent timed callouts. Each supports `text`, `position`, `font_size`, `color`, `background`, and an `animation` of `fade`, `slide-left`, `slide-up`, or `pulse`.
- `background_music.fade_in` and `background_music.fade_out`: track-local fade durations in seconds.
- `original_audio_volume`, `background_music.volume`, `intro.volume`, `outro.volume`, and `master_audio_volume`: independent mixer gain values.
- Audio volume values: `0.0` is silent, `1.0` is unchanged, and `2.0` is double gain.

The included **Bunker Build Documentary** preset uses four animated story beats and horizontal mirroring. The Audio editor includes three original local tracks—**Cinematic Build**, **Bunker Pulse**, and **Deep Focus**—plus custom music upload. Original video, background music, and master mix volumes remain independently adjustable.

## Processing behavior

The existing advanced editor keeps one in-memory render queue and processes its items sequentially. FFmpeg progress is reported to the UI approximately every two seconds. Each job writes to its own output file. If FFmpeg fails, the partial output is removed, the error is attached to that video, and the batch proceeds to the next item.

If the containers restart, source and output files and all automation records remain. Interrupted automation acquisition/render work is recovered and safely retried using deterministic paths and durable asset records. The advanced editor's live render/download activity is still reset; submit unfinished editor jobs again from that workspace.

Before rendering, the backend checks available output-disk space. Every render uses a hidden `.rendering.mp4` file that is removed on failure or cancellation and renamed only after FFprobe validates the selected dimensions. Batch failures remain isolated. Six-second production previews are keyed by source and settings, reused as a render cache, and removed after 24 hours; projects, presets, beat assets, analysis cache, inputs, and outputs are mounted persistently.

## Docker audio dependency

`librosa==1.0.0` supplies stable local onset-strength, tempo, and dynamic-programming beat tracking on Python 3.12. FFmpeg first decodes video/audio to temporary 22.05 kHz mono PCM for consistent analysis; generated deliverables remain 48 kHz stereo. NumPy and soundfile arrive as librosa dependencies and are used for deterministic procedural synthesis and WAV writing. The durable control plane adds SQLAlchemy, Alembic, and Psycopg for PostgreSQL persistence and versioned migrations. No media-analysis network service, sample pack, or model download is used.

Rebuild after dependency changes:

```bash
docker compose up -d --build
docker compose ps
```

`shorts-studio-postgres`, `shorts-studio-backend`, and `shorts-studio-frontend` should be running; PostgreSQL and the backend should report `healthy`.

## Rights-safe use

Use only video, dialogue, music, sound effects, logos, and other assets you own or are licensed to edit. Generated beat metadata records that the audio was synthesized locally without third-party samples. This project does not bypass Content ID, remove watermarks, evade copyright detection, or make third-party media “undetectable.” Lowering or ducking music is a creative mixing choice, not a copyright workaround.

## Configuration

The optional `.env` file supports:

- `APP_PORT` — browser UI port, default `3000`
- `API_PORT` — direct FastAPI port, default `8000`
- `FFMPEG_CRF` — H.264 quality, default `20` (lower is higher quality)
- `FFMPEG_PRESET` — encoding speed/compression tradeoff, default `medium`
- `FFMPEG_VIDEO_ENCODER` — `libx264` (default), `auto`, `h264_nvenc`, `h264_qsv`, or `h264_amf`. Hardware choices are tested with a real probe encode once per backend start; an unavailable device or encoder automatically falls back to CPU `libx264`.
- `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` — local control-plane database settings used by Docker Compose.
- `.secrets/google-oauth-client.json` — preferred ignored host path for the downloaded Google Web application client. Docker mounts it read-only at `/run/secrets/google-oauth-client.json`.
- `.secrets/oauth-token.key` — ignored deployment key generated locally and mounted read-only to encrypt OAuth tokens and resumable upload session URLs independently from the Google client secret.
- `YOUTUBE_PUBLISHING_ENABLED` — starts the durable YouTube publisher when true (default in Docker Compose).
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REDIRECT_URI` — non-Docker fallback configuration. Never commit real secrets.
- `OAUTH_TOKEN_ENCRYPTION_SECRET` — non-Docker fallback token-encryption key material. Docker uses the dedicated key file instead.

## Troubleshooting

- **Input list is empty:** confirm files end in `.mp4` and exist under `input/`, then click refresh.
- **Overlay/music is missing:** enable the option, upload the asset, save the preset, and render a new preview.
- **A video fails:** hover the failed row message for the FFmpeg error and check `docker compose logs backend`. Other queued items will continue.
- **Uploads fail immediately:** make sure port 3000 is serving the included Nginx configuration; it permits uploads up to 10 GB and disables request buffering to the backend.
- **Encoding is slow:** set `FFMPEG_PRESET=veryfast` in `.env`. This produces larger files but reduces CPU time.

## Development without the production frontend container

Run the backend with Docker, then start Vite locally if Node.js is installed:

```bash
docker compose up --build backend
cd frontend
npm install
npm run dev
```

Vite runs on port 5173 by default. Set `VITE_API_URL=http://localhost:8000` in the frontend environment for this development mode.
