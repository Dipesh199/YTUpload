# Current project capability inventory

**Project:** Local YouTube Remix Studio  
**Inventory date:** 2026-09-29  
**Purpose:** Establish exactly what the project does today before expanding it into automated multi-channel publishing.  
**Target deployment:** Private, single-operator use. Multi-user tenancy, team roles, and formal ownership/licensing management are out of scope.

## Executive summary

The project is currently a capable **local video preparation, rendering, and scheduled YouTube publishing application** with persistent automation workers. It can import source videos, download authorized YouTube videos, preserve source metadata, edit through reusable presets and project-specific timelines, add audio and beat-synchronized effects, render previews, export finished MP4 files, and publish them through restart-safe YouTube resumable uploads. The automation dashboard registers destination-channel identities, securely OAuth-connects each exact YouTube channel, validates destination-first URL batches, persists immutable work records in PostgreSQL, and automatically downloads, remixes, validates, uploads, and confirms YouTube processing for every accepted item.

It now implements the first YouTube publishing milestone for a trusted local operator. It does not yet provide a calendar, approval workflow, ongoing post-publication monitoring, analytics, quota dashboards, or large-scale multi-worker orchestration.

In short:

| Area | Current status |
| --- | --- |
| Source media import | Implemented |
| YouTube video download | Implemented for individual authorized videos |
| Source title/description/tags capture | Implemented and persistent for new YouTube downloads |
| Video editing and reusable presets | Implemented |
| Long-form and Shorts rendering | Implemented |
| Beat creation, analysis, mixing, and synchronized effects | Implemented |
| Local batch rendering | Implemented with a single sequential queue |
| Automation dashboard shell | Implemented for the single local administrator |
| Destination-first URL batches | Implemented through validation and durable queue creation |
| Source-channel imports | Implemented for full Videos/Shorts/Streams-tab discovery, duplicate-safe durable queue creation, and manual reconciliation |
| Persistent automation database | Implemented with PostgreSQL and Alembic migrations |
| Durable automation acquisition/render worker | Implemented with claiming, restart recovery, progress, failure isolation, and retry |
| YouTube account connection | Implemented with server-side OAuth, exact-channel verification, encrypted token storage, test, reconnect, and revoke |
| YouTube upload and scheduling | Implemented with resumable upload, privacy/schedule metadata, processing polling, and durable progress |
| Multiple channel profiles | Partially implemented: independent identities, OAuth connections, publishing queues, cadence, limits, and timezone; advanced policy/defaults remain future work |
| Persistent publishing job records | Implemented with encrypted resumable sessions, restart recovery, audit events, and retry |
| Analytics and post-publication monitoring | Not implemented |

## Durable automation foundation

### Implemented

- The automation dashboard is now the default interface; the complete existing editor remains available through **Advanced editor**.
- Docker Compose defines a persistent PostgreSQL service and named data volume.
- Alembic runs versioned migrations before the backend starts.
- Durable models exist for destination channels, encrypted Google connections, one-time OAuth authorization states, URL batches, batch items, immutable pipeline versions, media assets, render jobs, publish jobs, resumable upload sessions, and append-only automation events.
- A destination channel is registered by display name and stable YouTube channel ID. It starts `not_connected` and `paused`; **Connect with Google** verifies the selected Google identity. An exact match connects immediately. A different authorized channel is shown by its real title and channel ID and remains pending until the operator explicitly adopts or discards it.
- OAuth uses a backend-only Web application client, offline access, one-time hashed state with a 10-minute expiry, an encrypted persisted PKCE verifier, encrypted access/refresh tokens, and scopes needed for future YouTube upload, thumbnail, and playlist work. Client credentials are mounted read-only from the ignored `.secrets` directory.
- Each destination can be connected, tested, reconnected, disconnected, or safely removed from active Channels independently. Connected destinations must be disconnected before removal. Removal disables and archives the destination, invalidates pending OAuth state, and preserves its internal UUID, existing batches, jobs, and audit history.
- A confirmed channel adoption updates only the destination's stable YouTube ID, preserves its internal UUID and existing batches, prevents duplicate destination identities, activates encrypted credentials, unblocks eligible publish jobs, and records an append-only audit event. Discarded or superseded pending credentials are revoked when possible and always removed locally.
- The **New URL Batch** workflow requires a destination before enabling URL entry.
- The **Source channels** workflow requires a destination, accepts YouTube channel and `@handle` links, merges public items across the Videos, Shorts, and Streams tabs without downloading media during discovery, and creates durable work only for source/destination pairs not already recorded.
- Source-channel configurations and their last discovery result persist in PostgreSQL. **Sync new videos** repeats metadata discovery safely and skips previously queued videos.
- Newline-, comma-, and space-separated YouTube URLs are parsed and normalized to stable video IDs and canonical watch URLs.
- Standard watch, short-link, Shorts, live, embed, and YouTube-nocookie URL forms are supported.
- Invalid URLs, repeats inside one pasted list, and videos already recorded for the same destination are identified before batch creation.
- Preview rows show the normalized identity, thumbnail URL, acceptance state, and duplicate/validation reason.
- Batch settings persist an immutable snapshot of the remix preset, output profile, metadata behavior, visibility, first publication time, publication interval, playlist IDs, thumbnail behavior, subscriber notification, source cleanup toggle, and export retention period.
- The dashboard defaults new URL batches and source-channel imports to scheduled public publication. The only operator choices are **Scheduled public** and **Public immediately**; legacy private or unlisted API input is normalized to public. Scheduled visibility requires a first publication time and defaults to 30 minutes between public releases.
- Accepted items reserve unique, durable render and publish idempotency keys plus an upload-session record.
- A durable worker claims accepted items, downloads each source with `yt-dlp`, preserves its metadata sidecar, applies the batch's frozen remix preset and output profile through the existing FFmpeg engine, validates the result, and records the altered export as a media asset.
- Deterministic source/export filenames, unique per-item asset records, leases, restart recovery, and committed state transitions make the local single-worker path idempotent and restart-safe.
- Queue progress refreshes automatically. Completed items expose an altered-video download; failed download or render jobs retain source files and expose a manual retry action.
- The queue displays batch, item, altered-export, and publish percentages. Once a source is retained, **Edit video** opens that exact source in the existing advanced editor. **Save video** stores the current editor settings and timeline in a new immutable pipeline version, durably requeues the item, and atomically replaces the altered export only after validation succeeds.
- Every scheduled queue row displays its durable YouTube public-release date, local time, and timezone. Schedule adjustments made when upload starts are persisted on the publish job and appear automatically in the refreshing queue.
- Each batch has a durable **Pause processing** / **Resume processing** control. Pausing lets the current acquisition or alteration finish, prevents the render worker from claiming the next item, survives restarts, and does not block uploads of exports that are already ready.
- Destination channels store a 15-minute fallback cadence for immediate public uploads. Scheduled uploads bypass it; Overview shows their YouTube public-release time and countdown instead.
- Export retention timestamps are recorded. Configured source cleanup runs only after YouTube confirms processing and all configured post-upload actions succeed.
- Publish jobs remain `blocked_not_connected` until their destination is authorized, then move through `waiting_for_upload`, `uploading`, `youtube_processing`, and `scheduled` or `published`. Failed upload/post-publish work exposes an explicit retry without creating another YouTube video after an ID has been recorded.
- Each successful render independently makes its publish job eligible immediately; it does not wait for the rest of its batch. Scheduled jobs upload as soon as the single publisher is free and ignore the channel upload-start cooldown because YouTube controls their later release time. Immediate public jobs still use the destination cadence.
- Scheduled batches assign a distinct future publication slot to every accepted item. Before upload, stale slots are moved forward and kept at the configured interval from already committed YouTube schedules. YouTube receives the video as private with `publishAt`, then makes it public at that time without requiring this application to stay online.
- A channel visibility enforcement operation verifies every unscheduled uploaded video against YouTube, changes private/unlisted uploads to public, normalizes pending jobs to public, preserves scheduled videos and their `publishAt` times, reconciles scheduled videos already made public on YouTube, and records append-only audit events. Individual failures remain unchanged and are reported rather than presented as successes.
- Existing batches created with another visibility can explicitly move all not-yet-uploaded jobs to 30-minute scheduled-public slots. Already uploaded or actively uploading videos are left unchanged, and source-feed settings are updated for later reconciliations.
- The publisher verifies the exact authorized channel before each external write, uploads fixed 8 MiB chunks, encrypts the resumable session URL at rest, persists acknowledged bytes, probes the server offset after restart, and stores the YouTube video ID before polling processing.
- Each publish job is capped at five total attempts. Processing checks for an already uploaded video are spaced by 30 seconds, and retries continue against the persisted YouTube video ID rather than creating a duplicate upload.
- Frozen title, description, tags, privacy/schedule, subscriber-notification, thumbnail, and playlist settings are applied. Long-form outputs can reuse the source thumbnail; Shorts always leave thumbnail selection to YouTube and never call the custom-thumbnail API. Playlist insertion checks for an existing item before writing, so a retry does not duplicate it.
- Queue and history views are database-backed and survive backend restarts.
- The API exposes automation overview, destination channel, OAuth start/callback/verify/revoke, URL preview, batch, queue, and event-history operations.

### Current limitations

- The Google Auth app is currently in Testing mode, so only configured test users can authorize and Google may require reauthorization after seven days.
- Publishing uses a single local worker, so upload byte transfers are still sequential even though scheduled jobs have no artificial start delay. Daily counters are stored, but automatic date-boundary reset and quota-aware backoff are not implemented yet.
- Source-channel imports run when a feed is added or **Sync new videos** is clicked. Push notification subscriptions and periodic background reconciliation are not implemented yet.
- The existing editor render queue and individual YouTube download activity remain in memory; this slice deliberately does not replace those working paths.
- The dashboard is intended for a trusted local operator and does not yet enforce an administrator login/session.

## 1. Current end-to-end workflow

The advanced editor currently supports this complete workflow:

1. Add an MP4 by browser upload, folder selection, direct placement in `input/`, or an authorized YouTube download.
2. Select the source in the Media library.
3. Choose or edit a reusable JSON preset.
4. Configure framing, text, graphics, effects, audio, beat behavior, and output format.
5. Optionally analyze the source audio or generate an original beat.
6. Build a source cut list and arrange additional video, image, voiceover, music, and sound-effect clips on the timeline.
7. Save the video-specific work as a project.
8. Render and inspect a six-second FFmpeg preview.
9. Process one or several sources through the local queue.
10. Download the finished MP4 from the Exports library.

The automation dashboard additionally accepts destination-first URL batches, downloads accepted sources, renders them with the frozen preset/settings, and publishes validated altered exports to the exact connected destination. **Work queue** shows durable upload and YouTube processing progress and links completed videos back to YouTube.

## 2. Media ingestion and library

### Implemented

- Upload one or multiple `.mp4` files from the browser.
- Select a browser folder containing MP4 files.
- Discover MP4 files recursively from the mounted `input/` directory.
- Stream a source video to the browser for playback.
- Generate and cache a poster frame for each source.
- Display source duration, dimensions, frame rate, and file size.
- Select individual sources or select all sources for batch operations.
- Delete one source or several selected sources.
- Prevent deletion while a source is actively queued or rendering.

### Current limitations

- The primary source library accepts MP4 files only.
- There is no cloud object storage, remote media library, or media database.
- Media identity is derived from the source path. Renaming or moving a file changes its ID.
- There is no duplicate-content detection beyond output filename collision prevention.

## 3. YouTube source download and metadata

### Implemented

- Validate `youtube.com` and `youtu.be` URLs.
- Intentionally restrict each request to one video rather than a playlist.
- Download through `yt-dlp` with retry handling.
- Prefer browser-compatible H.264 MP4 video and M4A audio streams.
- Merge or remux the result into MP4.
- Display progress percentage, transfer speed, ETA, post-processing status, errors, cancellation, and retry.
- Clean up partial files after failure or cancellation.
- Capture and persist these source fields for new downloads:
  - YouTube video ID
  - source URL
  - title
  - description, including any original hashtags
  - tags/keywords
  - category names
  - channel name and channel ID
  - original upload date
  - metadata capture timestamp
- Save the extracted values beside the MP4 as `<video>.mp4.metadata.json`.
- Return `source_metadata` with Media API records.
- Preserve the source metadata sidecar when creating an export.
- Return the same metadata with Exports API records, ready to prefill a future publishing form.
- Show the saved title and description in the download interface.
- Remove the metadata sidecar when its associated source or export is deleted.

### Current limitations

- Previously downloaded or manually uploaded MP4 files are not retroactively enriched from YouTube.
- Download activity is kept in memory and is cleared by a backend restart, although completed files and metadata sidecars remain.
- The feature is intended only for videos the operator owns or has permission to use.
- Downloading does not connect or authenticate a YouTube channel for publishing.

## 4. Reusable templates and visual editing

### Implemented

- Create, edit, save, list, import, and delete JSON presets.
- Keep reusable templates separate from video-specific projects.
- Choose an output profile:
  - YouTube Landscape, 1920×1080
  - YouTube 1440p, 2560×1440
  - YouTube Shorts, 1080×1920
  - Preserve Source
- Preserve the original source frame rate.
- Use `cover` or `contain` scaling.
- Control zoom and crop focal point.
- Mirror horizontally or vertically.
- Adjust contrast, brightness, saturation, gamma, and sharpening.
- Add positioned text with deterministic wrapping.
- Add multiple timed animated text/callout elements.
- Use fade, slide-left, slide-up, or pulse callout animation.
- Add logo, PNG, and watermark overlays.
- Add minimal, double, neon, or cinematic decorative frames.
- Upload reusable image, audio, and video assets into the template asset library.
- Configure intro and outro clips.
- Render browser approximations and a real six-second FFmpeg preview.

### Included reference content

- `templates/long-form-beat-remix.json`
- `templates/bunker-build-documentary.json`
- Three local background tracks: Cinematic Build, Bunker Pulse, and Deep Focus
- The Long-Form Beat Remix preset layers those tracks across timed sections, adds three original local transition/impact sound effects, and enables restrained beat-synchronized accents, grading, a double frame, and a watermark.
- Five generated default beat families with master mixes and stems

## 5. Timeline and saved projects

### Implemented

- Save, load, update, list, and delete video-specific projects.
- Store source identity, selected template, editor settings, beat analysis, current timeline time, and timeline clips.
- Build an ordered source cut list.
- Trim clips with in/out points.
- Split, delete, and reorder clips.
- Append B-roll video or still images.
- Use cut, fade, or dissolve transitions.
- Undo and redo timeline edits with a bounded history.
- Display a zoomable timeline.
- Display cached waveform, beat, and downbeat markers.
- Drag and trim timed overlays and audio lanes.
- Snap relevant edits to the beat grid.
- Store forward-compatible unknown project fields.
- Write projects atomically to reduce the chance of partial JSON files.

### Current limitations

- Projects are JSON files rather than database records.
- There is no project collaboration, revision history, ownership, or locking.
- There is no proxy-media workflow for very large sources.
- There is no transcript, caption editor, or scene-detection pipeline yet.

## 6. Audio, beat analysis, and beat generation

### Implemented

- Analyze source audio locally with FFmpeg and librosa.
- Estimate BPM and beat timestamps.
- Estimate a heuristic four-beat downbeat grid.
- Generate a 512-point normalized waveform.
- Cache analysis by source path, file size, and modification time.
- Correct BPM and offset manually.
- Estimate tempo through a tap-tempo control.
- Generate deterministic, sample-free local beats.
- Configure BPM, bars, intensity, swing, density, bass level, percussion level, and seed.
- Produce a combined 48 kHz stereo WAV plus kick, snare, percussion, bass, and atmosphere stems.
- Cancel beat generation without publishing partial beat folders.
- Loop a generated beat or its stems under the source.
- Mix independent stem levels.
- Automatically duck a beat beneath source dialogue/audio through side-chain processing.
- Add independent voiceover, music, and sound-effect lanes.
- Configure timing, volume, loop, mute, solo, and fades per audio lane.
- Mix source audio, background music, beat stems, additional audio, intro, and outro.
- Optionally normalize toward -14 LUFS and apply peak limiting.

### Current limitations

- Downbeat detection is heuristic rather than semantic music analysis.
- Automatic ducking uses the audio signal; it does not transcribe or understand speech.
- There is no text-to-speech, voice cloning, automatic narration, or speech-to-text system.
- There is no music licensing catalog or rights-clearance service.

## 7. Beat-synchronized video effects

### Implemented distinctly in FFmpeg rendering

- Twinkling stars
- Sparkle burst
- Cinematic bars
- Punch zoom
- Subtle camera shake

### Present as named effect types but currently approximated

- `radial_blur_pulse` currently uses the same scale/crop pulse family as punch zoom; it does not apply a true radial blur.
- `glow_pulse`, `light_leak_sweep`, `animated_gradient`, `vignette_pulse`, `film_grain_accent`, and `chromatic_split` currently share a timed brightness/saturation treatment rather than distinct visual implementations.
- `caption_bounce`, `shape_burst`, `motion_lines`, `progress_indicator`, and `section_title_reveal` currently fall back to a timed full-frame color accent in the final FFmpeg render.

Effects can be triggered manually, on downbeats, on every beat, or every N beats. They share the same BPM and offset used by the editor timeline. Reduced-flashing mode limits intensity and opacity by default.

### Preview limitation

The browser live preview represents stars, sparkle bursts, and cinematic bars directly. Other effects are verified most accurately through the real six-second FFmpeg preview and final render.

## 8. Rendering, outputs, and job handling

### Implemented

- Render with FFmpeg and validate with FFprobe.
- Produce H.264 video, AAC stereo audio, and web fast-start metadata.
- Use CPU `libx264` by default.
- Optionally probe and use NVENC, Intel QSV, or AMD AMF, with automatic CPU fallback.
- Configure CRF and CPU preset through environment values.
- Check available output disk space before rendering.
- Write to a hidden temporary render file.
- Publish the final output only after duration and dimensions validate.
- Remove partial files after failure or cancellation.
- Generate collision-resistant output names so repeated exports never overwrite one another.
- Process selected inputs or all inputs.
- Continue the batch after an individual item fails.
- Cancel one queued/active render or the entire current batch.
- List completed outputs with source, preset, duration, size, timestamp, and download link.
- Delete completed outputs.

### Current limitations

- Rendering uses one in-memory sequential queue protected by a single queue lock.
- It does not distribute work across multiple workers or machines.
- Queue state is lost when the backend restarts.
- There is no scheduled render time, job priority, retry policy, or dead-letter queue.
- The advanced editor's standalone in-memory queue does not automatically hand off its ad hoc exports to the automation publisher; destination-first automation batches do.

## 9. Current application architecture

| Layer | Current implementation |
| --- | --- |
| Frontend | React single-page workspace built with Vite and served by Nginx |
| Backend | FastAPI application |
| Video processing | FFmpeg and FFprobe |
| YouTube source import | `yt-dlp` |
| Beat analysis | librosa |
| Beat synthesis | Local Python/NumPy/soundfile generation |
| Deployment | Docker Compose with frontend and backend containers |
| Persistent state | Mounted media folders plus PostgreSQL automation records |
| Temporary state | Advanced-editor job dictionaries and a disposable preview-cache volume |
| Database | PostgreSQL 18 with versioned Alembic migrations |
| User authentication | None |
| External cloud dependency | None for editing/rendering; network is used only for requested YouTube downloads |

## 10. Persistent data model

| Host path | Stored data |
| --- | --- |
| `input/` | Source MP4 files and YouTube metadata sidecars |
| `output/` | Finished MP4 exports and preserved metadata sidecars |
| `templates/*.json` | Reusable editing presets |
| `templates/assets/` | Uploaded images, audio, video assets, and included music |
| `templates/projects/` | Video-specific project JSON files |
| `templates/assets/beats/` | Generated beat masters, stems, and metadata |
| `templates/assets/analysis/` | Cached BPM, beat-grid, and waveform analysis |
| Docker volume `postgres-data` | Destination channels, batches, items, pipeline versions, jobs, sessions, and events |

Inputs, outputs, projects, templates, metadata, beats, analysis, and automation job state survive container rebuilds. Only the advanced editor's live job/download history remains in memory.

## 11. Available backend API groups

The backend currently exposes these functional API groups:

| Group | Operations |
| --- | --- |
| Health | Runtime readiness and FFmpeg/yt-dlp availability |
| Videos | List, upload, stream, generate poster, and delete |
| YouTube downloads | Start, list current-session jobs, and cancel |
| Templates | List, create, update, and delete |
| Output profiles | List supported render profiles |
| Projects | List, read, create, update, and delete |
| Beats | List, generate, inspect generation, cancel, and delete |
| Beat analysis | Start, inspect, and cancel |
| Assets | Upload and retrieve reusable assets |
| Processing | Start a batch and cancel an active job |
| Preview | Render and retrieve cached previews |
| Outputs | List, download, and delete exports |
| Automation | Overview, channel registration, secure Google OAuth connection controls, URL preview/normalization, full source-channel discovery and reconciliation, immutable batch creation, durable acquisition/render/publish queues, resumable upload progress, altered-export download, failed-job retry, and event history |

The automation APIs cover destination-channel records, Google OAuth connection lifecycle, immutable batch settings, publishing retry, and database-backed progress. The publisher performs video upload, long-form source-thumbnail upload, playlist insertion, scheduled-private publication metadata, and processing polling. Shorts use YouTube-selected thumbnails and do not call the custom-thumbnail API. Comments, captions, analytics, and ongoing post-publication monitoring are not implemented.

## 12. Security and operational behavior

### Implemented safeguards

- Sanitize generated filenames.
- Constrain project and asset paths to configured storage roots.
- Validate supported YouTube hostnames before download.
- Limit YouTube imports to one video per request.
- Avoid publishing partial beats or partial video exports.
- Keep editing and rendering local.
- Include explicit rights-safe-use guidance.
- Validate one-time OAuth state and expiry before exchanging an authorization code.
- Require either an exact authorized YouTube channel ID match or explicit operator confirmation before changing the destination's stable channel ID.
- Persist a discovered mismatched identity and its tokens only as an encrypted, expiring pending candidate; prevent use until confirmation and remove it on rejection or abandonment.
- Encrypt Google access and refresh tokens at rest using deployment-managed secret material.
- Keep the OAuth client JSON in an ignored host directory and mount it read-only into the backend container.
- Disable backend HTTP access logging so one-time OAuth callback codes are not written to container logs.

### Gaps before broader deployment

- The app has no login, roles, tenant isolation, or per-user storage.
- Local API access is not designed as a hardened public internet service.
- Versioned migrations are implemented; database backup and restore automation is not.
- There is no centralized audit log, metrics service, alerting, or structured operational dashboard.
- Backend URL/control-plane tests and frontend production-build checks are available; worker media processing is also runtime-verified with FFprobe.

## 13. What is not implemented yet

The following items should be treated as future work, not current capabilities:

- Advanced per-channel publishing policies, approvals, and quota-aware scheduling across many destinations
- Setting language, audience, altered-content disclosure, license, or other advanced YouTube fields during upload
- Uploading operator-selected custom thumbnail files or generated thumbnails; the current publisher supports the preserved source thumbnail
- Channel-specific metadata templates, branding, defaults, quotas, and approval rules
- Content calendars and recurring publishing schedules
- Post-publication status, processing, copyright-claim, or failure monitoring
- YouTube Analytics ingestion and reporting
- Comment, moderation, community, or monetization management
- Cross-channel duplicate detection and publishing conflict prevention
- Multi-user approvals or review workflows

## 14. Readiness for multi-channel expansion

The strongest reusable foundations are already present:

- A working media and export pipeline
- Structured source metadata
- Reusable templates
- Video-specific projects
- Background job concepts with progress and cancellation
- Deterministic output naming
- Persistent mounted storage
- A frontend workspace and backend API boundary

The main architectural work still required after the durable control-plane foundation is:

1. **Channel profiles:** expand the connected identity with metadata templates, playlists, category/language defaults, thumbnail rules, and publishing policy.
2. **Publishing editor:** prefill from preserved source metadata, validate YouTube limits, preview changes, and require explicit approval where configured.
3. **Publishing policy:** approvals, richer metadata editing, quota-aware backoff, daily-counter reset, and calendar controls.
4. **Operations layer:** structured logs, alerts, backup/restore, ongoing video monitoring, and analytics.

## 15. Recommended scope boundary for the next phase

A safe first publishing milestone is now implemented:

- One local operator
- Multiple connected YouTube channels
- One explicit destination channel per upload
- Editable metadata prefilled from `source_metadata`
- Scheduled-public dashboard workflow with a configurable first release and 30-minute default spacing
- Resumable upload with persistent progress and retry
- Optional thumbnail and playlist selection
- Upload history stored in a local database
- Durable per-batch render pause/resume while completed exports continue uploading

Calendar editing, analytics, and large-scale channel orchestration can build on the same persistent channel and job model.

## Future automation blueprint

The planned destination-first URL batches, multi-channel dashboard, continuous source pipelines, durable workers, YouTube upload flow, scheduling, and source-cleanup behavior now live in [`MULTI_CHANNEL_AUTOMATION_BLUEPRINT.md`](MULTI_CHANNEL_AUTOMATION_BLUEPRINT.md).

> **Required reading:** Before implementing automation, dashboard, ingestion, rendering-worker, cleanup, OAuth, scheduling, or publishing changes, read both this current-state audit and `MULTI_CHANNEL_AUTOMATION_BLUEPRINT.md`.

## Related project documentation

- [`MULTI_CHANNEL_AUTOMATION_BLUEPRINT.md`](MULTI_CHANNEL_AUTOMATION_BLUEPRINT.md) — complete future system and implementation sequence
- [`README.md`](README.md) — setup, normal workflow, configuration, and operational behavior
- [`TEMPLATE_GUIDE.md`](TEMPLATE_GUIDE.md) — complete reusable template format
- [`PROJECT_JSON_GUIDE.md`](PROJECT_JSON_GUIDE.md) — video-specific saved project format
- [`BEAT_TEMPLATE_GUIDE.md`](BEAT_TEMPLATE_GUIDE.md) — beat generation, analysis, stems, mixing, and effects
