# Multi-channel automation blueprint

**Project:** Local YouTube Remix Studio  
**Plan date:** 2026-09-28  
**Deployment:** Private, single-operator system  
**Status:** Target design; the current implementation is documented separately in `CURRENT_CAPABILITIES.md`.

> **Required reading for future work:** Before changing the dashboard, source ingestion, remix processor, channel integration, scheduling, cleanup, or upload pipeline, read this file and `CURRENT_CAPABILITIES.md`. Treat this file as the product and architecture baseline unless the user explicitly changes the plan.

## 1. Product vision

The main product will be an automation dashboard for operating multiple YouTube destination channels. The current video remix editor remains available, but it becomes a background render engine and an optional advanced editor rather than the primary interface.

The system must support two ingestion modes:

1. **URL-list batches:** The owner selects a destination channel first, pastes one or many YouTube links, chooses the remix and publishing settings, and starts an automated batch.
2. **Continuous source pipelines:** The owner configures one or more source channels for a destination channel. New detected items enter that destination's configured pipeline automatically.

No multi-user tenant system, team roles, or formal licensing/ownership-management module is planned. This is one owner's private operating tool.

## 2. Primary URL-list workflow

This should be the first complete automation workflow.

### Step 1 — choose the destination channel

Before the dashboard accepts a URL list, it asks:

> Which YouTube channel should receive these edited videos?

The selector shows:

- channel avatar and display name
- stable YouTube channel ID
- connected Google identity
- connection/token health
- enabled/paused state
- today's upload count and configured limit
- next available publishing slot

The chosen destination is stored on the batch and cannot silently change. To send links to a different channel, create another batch. A future per-row override can be added, but the initial design keeps one destination per batch to prevent mistakes.

### Step 2 — paste and validate links

The owner pastes newline-, comma-, or space-separated YouTube video URLs.

The dashboard:

- normalizes each URL to a stable video ID
- rejects unsupported or malformed URLs
- removes duplicates inside the pasted list
- detects items already processed for that destination
- fetches available title, description, channel, duration, thumbnail, and publication metadata
- shows a preview table before creating work
- allows removal of individual rows

### Step 3 — choose pipeline settings

The batch form provides:

- remix template and immutable template version
- output format: 1080p, 1440p, Shorts, or preserve source
- beat/music/effect configuration
- destination branding pack
- metadata mode: preserve, template, rewrite, or manual
- long-form thumbnail mode; Shorts always use YouTube's automatic thumbnail selection
- playlist destinations
- visibility policy: public immediately or scheduled public (uploaded privately with `publishAt` because YouTube requires it)
- publishing cadence, timezone, and subscriber notification preference
- approval mode
- **Delete downloaded source after successful upload** toggle, enabled by default

### Step 4 — confirm the batch

The confirmation page clearly displays:

- destination channel
- number of accepted links
- selected remix preset
- visibility and schedule
- upload spacing and daily cap
- cleanup behavior
- duplicate/skipped count

After confirmation, the batch receives an immutable ID and settings snapshot.

### Step 5 — run automatically

Each accepted link progresses independently:

```text
queued
  -> metadata_fetched
  -> source_downloading
  -> source_ready
  -> render_queued
  -> rendering
  -> render_validating
  -> review_pending / approved
  -> upload_queued
  -> uploading
  -> youtube_processing
  -> thumbnail_and_playlist
  -> scheduled / published
  -> source_cleanup
  -> completed
```

Exceptional states:

```text
duplicate
skipped
paused
cancelled
source_failed
render_failed
upload_failed
youtube_rejected
cleanup_failed
quarantined
```

One failed item must not stop unrelated items in the batch.

## 3. Source deletion and retention policy

The requested default is to delete the downloaded source video after a successful upload.

### Safe deletion trigger

Do not delete the downloaded source merely because all bytes were sent. Delete it only after all of these are true:

1. The returned YouTube video ID is persisted.
2. YouTube reports the uploaded video as successfully processed rather than failed or rejected.
3. Required metadata, thumbnail, and playlist steps have completed or have been explicitly marked non-blocking.
4. The rendered output checksum and publication event are stored.
5. No retry or manual review still depends on the source.

### Files deleted when source cleanup succeeds

- downloaded source MP4
- source metadata sidecar
- temporary fragments and remux files
- disposable render intermediates
- cached source-only previews when no other project references them

### Files retained by default

- final edited/exported video
- thumbnail used for upload
- small metadata and event records
- YouTube video ID and URL
- checksums, processing summary, errors, and audit history

Deleting the final edited export must be a separate option. Recommended behavior is to retain it for a configurable period, such as 7–30 days, then clean it with a retention job.

### Failure behavior

- Failed, rejected, cancelled, or uncertain uploads keep the downloaded source.
- If the backend crashes after upload but before cleanup, it reconciles the persisted YouTube video ID and processing status before deleting anything.
- Cleanup is idempotent: retrying it must be safe when some files are already absent.
- A `cleanup_failed` state creates an alert but must never start another YouTube upload.
- The database keeps a tombstone record after file deletion so duplicate detection and history still work.

## 4. Continuous source-channel pipelines

After URL-list batches are reliable, add continuous source pipelines.

### Configuration flow

1. Select the destination channel first.
2. Create a pipeline name.
3. Add one or more source channel IDs/URLs.
4. Configure source filters and lookback date.
5. Choose the source acquisition connector.
6. Choose remix, metadata, thumbnail, and publishing rules.
7. Choose approval and cleanup behavior.
8. Test with one item in private visibility.
9. Activate the pipeline.

The destination is stored on the pipeline. Every detected item inherits it; the automation never guesses a destination channel.

### Source filters

- start/lookback date
- include/exclude keywords
- include/exclude source channel
- duration range
- Shorts, long-form, or both
- language
- maximum items per source per day
- maximum age of a source item
- skip already delivered source/destination pairs

### Detection

Use YouTube push notifications for near-real-time upload/title/description events. Also run periodic uploads-playlist reconciliation to catch missed notifications and initial backfill. Webhook redelivery and reconciliation must be idempotent.

### Source acquisition

The official YouTube Data API does not provide media downloads. The system therefore separates detection from acquisition.

Supported connector targets should include:

- existing local `yt-dlp` importer as an explicitly configured local connector
- watched local folder or NAS
- S3/MinIO object storage
- Google Drive/cloud folder
- signed/private HTTPS feed
- manual dashboard upload fallback

The local `yt-dlp` connector is not an official YouTube API download mechanism and may require maintenance when YouTube changes media delivery. YouTube's API policy limitations still apply even though this is a private tool.

## 5. Dashboard information architecture

### Overview

- global system state and emergency stop
- connected destination channels
- active/paused/error pipelines
- queue counts by stage
- current renders and uploads
- today's completed/failed uploads per channel
- upcoming publication calendar
- quota, storage, worker, webhook, and credential health
- alerts requiring action

### Channels

Each destination channel page includes:

- identity, stable channel ID, avatar, and OAuth health
- Start, Pause new work, Drain, Resume, Disable, and Emergency stop
- defaults for privacy, category, language, tags, notification, thumbnail, and playlists
- publishing timezone, cadence, blackout windows, and daily limit
- active pipelines and queued jobs
- upload history and YouTube links
- reconnect/revoke/test actions

### New URL batch

- destination-channel-first wizard
- multi-link paste box
- resolved-video preview table
- duplicate warnings
- preset and metadata controls
- schedule controls
- cleanup toggle
- final confirmation

### Source feeds

- source channel ID/URL
- enabled/trusted status
- acquisition connector and master location
- webhook and reconciliation health
- most recent detected item
- filters and backfill date
- destination pipelines using the source
- Enable, Pause, Reconcile now, Test acquisition, Edit, and Remove

### Pipelines

- visual source-to-destination mapping
- active and draft versions
- remix/template version
- metadata and publishing policy
- cleanup/retention policy
- validation results
- dry run and private test
- version diff, activate, and rollback

### Work queue

- filter by channel, batch, pipeline, source, state, and date
- live progress and current stage
- source and rendered preview
- metadata comparison
- attempts and normalized errors
- Approve, Reject, Edit, Pause, Retry stage, Cancel, Quarantine, or Republish
- bulk controls with confirmation

### Calendar

- combined and per-channel views
- timezone-aware scheduled slots
- drag-to-reschedule with conflict validation
- channel cadence and daily caps
- queued, uploading, processing, scheduled, published, and failed states

### History, logs, and alerts

- immutable event timeline for every item
- searchable worker logs
- channel/API response details without secrets
- token, quota, webhook, source, render, upload, processing, cleanup, storage, and schedule alerts
- exportable operating history

### Settings

- single-administrator login/session
- OAuth application status
- media storage and retention
- render/upload concurrency
- default cleanup policy
- alert destinations
- quota budget
- backup and restore controls

## 6. Start, pause, stop, and update behavior

| Control | Behavior |
| --- | --- |
| Start/Resume | Detect and dispatch eligible new items |
| Pause new work | Stop creating new jobs; current jobs continue according to policy |
| Drain | Stop intake and finish approved in-flight work |
| Stop after render | Finish rendering but do not upload |
| Stop before publish | Allow a private upload to finish but block scheduling/public release |
| Emergency stop | Prevent new external writes and cancel safe local work |
| Disable | Preserve configuration/history but prevent operation until re-enabled |

Pipeline edits create a new immutable version. New jobs use the new version; existing jobs retain the version that created them. Changing the destination, privacy, cleanup, or schedule requires explicit confirmation.

## 7. System architecture

```text
React automation dashboard
           |
           v
FastAPI control API
           |
           +---- PostgreSQL: channels, batches, pipelines, jobs, history
           |
           +---- Redis: task broker, short locks, live progress
                    |
        +-----------+------------+----------------+
        |                        |                |
        v                        v                v
 Source/acquisition workers   Render workers   Publish workers
 links, feeds, connectors     FFmpeg editor    YouTube API
        |                        |                |
        +------------------------+----------------+
                                 |
                                 v
                       Local/NAS/object storage

Scheduler/dispatcher
  -> due publications
  -> source reconciliation
  -> subscription renewal
  -> upload processing checks
  -> retention cleanup
```

### Recommended technology

| Concern | Recommendation |
| --- | --- |
| Frontend | Extend the existing React/Vite dashboard |
| API | Keep FastAPI and split it into domain modules |
| Database | PostgreSQL with versioned migrations |
| Task queue | Redis with Celery, Dramatiq, or one chosen durable Python worker framework |
| Scheduler | Database-backed dispatcher rather than in-memory timers |
| Media | Existing folders initially; storage adapter for NAS/S3/MinIO before multi-host scaling |
| Secrets | Encrypted OAuth refresh tokens and deployment-managed master key |
| Rendering | Existing FFmpeg processor behind isolated workers |
| Callbacks | HTTPS endpoints for OAuth and YouTube push notifications |

## 8. Core persistent records

| Record | Purpose |
| --- | --- |
| `app_settings` | Single-owner preferences, safety controls, and defaults |
| `google_connections` | Encrypted token reference, scopes, identity, health, and revocation |
| `youtube_channels` | Destination identity, channel defaults, limits, and operational state |
| `url_batches` | Destination-first batch and immutable settings snapshot |
| `source_feeds` | Source identity, connector, filters, cursor, and webhook state |
| `pipelines` | Editable automation identity and operational state |
| `pipeline_versions` | Immutable settings used by jobs |
| `source_items` | Source video IDs, URLs, metadata, and duplicate state |
| `media_assets` | Source, render, thumbnail, checksum, storage path, and retention state |
| `metadata_drafts` | Source, generated, approved, and published metadata versions |
| `render_jobs` | Progress, preset version, attempts, errors, and output artifact |
| `publish_jobs` | Destination, visibility, schedule, progress, and YouTube result |
| `upload_sessions` | Resumable URL, acknowledged bytes, recovery data, and last error |
| `published_videos` | YouTube ID/URL, processing, privacy, schedule, playlist, and cleanup state |
| `job_attempts` | Per-stage attempts and retry decisions |
| `pipeline_events` | Append-only state transition history |
| `alerts` | Actionable failure/health notifications |

Use unique constraints to prevent duplicate source/destination delivery. A suitable idempotency identity is:

```text
source provider + source video ID + destination channel ID + pipeline/batch identity
```

Retries, restarts, webhook redelivery, and reconciliation must not create another YouTube upload unless the owner explicitly chooses **Republish as new video**.

## 9. Destination-channel connection and upload

### OAuth

- Enable YouTube Data API v3 in a Google Cloud project.
- Use server-side OAuth 2.0; YouTube does not support service accounts for channel uploads.
- Request offline access and store refresh tokens encrypted.
- Resolve and store the exact authorized channel ID.
- Support reconnect, revoke, invalid-token handling, and connection tests.
- Never expose client secrets or refresh tokens to the browser or logs.

### Upload sequence

1. Lock one due publish job for its destination channel.
2. Validate credentials, channel state, quota budget, metadata, schedule, and artifact checksum.
3. Initiate a resumable upload and persist its session URL.
4. Upload recoverable chunks while storing acknowledged progress.
5. Recover the same session after network/process restart.
6. Persist the returned YouTube video ID immediately.
7. For long-form videos, upload the configured thumbnail; for Shorts, leave thumbnail selection to YouTube. Add configured playlists.
8. Poll YouTube processing status.
9. Mark scheduled/published only after successful processing.
10. Run source cleanup according to Section 3.

### Upload metadata

- title
- description and hashtags
- tags/keywords
- region-valid category ID
- metadata and audio language
- public-immediately or scheduled-public state
- made-for-kids declaration
- altered/synthetic-content disclosure
- YouTube licence and embeddability
- subscriber notification preference
- custom thumbnail for long-form videos; YouTube-selected thumbnail for Shorts
- playlist membership

Publish unscheduled videos publicly. Scheduling through `publishAt` requires an eligible private, never-published video, which YouTube makes public at the requested time. New/unverified API projects may be restricted to private API uploads until they pass YouTube's compliance audit.

## 10. Metadata automation

The existing downloaded metadata becomes the initial draft. Pipeline rules may then:

- preserve the source title
- add channel-specific prefixes/suffixes
- transform the title through a template
- preserve or rewrite the description
- append channel links and standard blocks
- preserve or regenerate tags
- extract and validate hashtags
- select category and language defaults
- insert optional attribution text

The dashboard always shows effective title, description, tags, category, privacy, and destination channel. It validates YouTube limits before upload. Automatic generation must never hide which final values will be published.

## 11. Render-worker conversion

The current editor becomes a worker service by:

- extracting processing logic from the monolithic API module
- accepting immutable source, template version, pipeline/batch version, and output-profile IDs
- recording structured progress and worker heartbeat
- supporting cancellation, timeout, and abandoned-job recovery
- storing FFmpeg/encoder details and output checksum
- enforcing CPU/GPU and disk-based concurrency
- preserving source metadata on the output artifact
- separating disposable previews from production artifacts

Automated validation before upload should check:

- expected file, duration, dimensions, codecs, and audio presence
- stable checksum
- black/silent output thresholds
- metadata limits and prohibited values
- destination channel still enabled
- no prior publication for the same idempotency key

## 12. Quota and scheduling governance

- Track the cost and daily budget of every YouTube operation.
- Reserve capacity before starting an upload.
- Enforce per-channel and global daily limits.
- Apply backpressure when the quota budget is low.
- Distinguish quota, rate, channel upload limit, authorization, validation, processing, and rejection errors.
- Forecast scheduled work in the dashboard.
- Never create extra API projects to evade limits.

Current official documentation states a default project allocation of 100 `videos.insert` calls, 100 `search.list` calls, and 10,000 daily units for other endpoints. Treat these as externally configurable limits, not permanent hardcoded capacity.

## 13. Reliability requirements

- PostgreSQL-backed job claiming with leases
- worker heartbeats and abandoned-job reclaim
- at-least-once task delivery with idempotent handlers
- bounded exponential backoff with jitter
- stage-specific retry limits, with no more than five publish attempts for the same video
- quarantine/dead-letter state instead of infinite retry
- graceful shutdown and draining
- resumable upload recovery
- atomic artifact publication and checksums
- per-channel upload serialization where needed
- global/channel/pipeline circuit breakers
- webhook deduplication
- database backups and restore tests
- low-disk safeguards and retention cleanup
- timezone-aware schedules and clock validation

## 14. Security and privacy

- Protect the dashboard with a single administrator login/session.
- Encrypt refresh tokens at rest.
- Keep OAuth client secrets and encryption keys outside source control.
- Use HTTPS for OAuth callbacks and source webhooks.
- Implement OAuth `state` protection, secure cookies, session expiry, and origin restrictions.
- Validate external URLs and constrain filesystem paths.
- Probe acquired media before passing it to FFmpeg.
- Provide reconnect/revoke and authorized-data deletion behavior.
- Keep audit history without logging tokens or secrets.

## 15. Testing and release gates

Automated coverage should include:

- URL parsing, normalization, and duplicate detection
- destination-channel locking
- pipeline versioning
- metadata limits
- schedule/timezone conflicts
- worker retry/reclaim
- render regression fixtures
- resumable-upload interruption and recovery
- token revocation
- quota exhaustion
- YouTube rejection and processing failure
- safe source cleanup and cleanup restart
- emergency-stop behavior

Do not enable unattended public publishing until uploads have passed duplicate, restart, quota, token, rejection, and cleanup simulations.

## 16. Implementation phases

### Phase 1 — durable dashboard foundation

- Add PostgreSQL and migrations.
- Add the single-admin dashboard session.
- Add channel, batch, pipeline, job, event, and alert records.
- Move job truth out of in-memory dictionaries.
- Build Overview, Channels, New Batch, Queue, and History pages.
- Wrap the existing renderer as a durable worker.

### Phase 2 — one destination channel and URL-list batch

- Connect one YouTube destination with OAuth.
- Implement the destination-first URL wizard.
- Add metadata validation and resumable upload with public-immediate and scheduled-public policies.
- Add thumbnail, playlist, and processing-status steps.
- Add safe source cleanup after confirmed processing.

This is the first useful end-to-end milestone.

### Phase 3 — multiple destination channels

- Store multiple OAuth connections/channel IDs.
- Add channel defaults, limits, branding, and independent controls.
- Enforce destination isolation and per-channel queues.
- Add scheduling calendar and publishing cadence.

### Phase 4 — continuous source pipelines

- Add push notification callback and renewal.
- Add uploads-playlist reconciliation.
- Add configured acquisition connectors.
- Add source filters, checksums, deduplication, and pipeline versioning.
- Connect detection to render and publish workers.

### Phase 5 — hardening

- Add metrics, alerts, backups, retention, and disaster recovery.
- Complete security and failure-path tests.
- Add quota forecasting and compliance operations.
- Complete any Google OAuth verification or YouTube API audit required for the intended scale.

### Phase 6 — analytics

- Record publication outcomes per destination channel.
- Add permitted channel-level performance reporting.
- Compare pipeline results and help the owner improve schedules and templates.

## 17. Completion criteria

The system is complete when it can:

- connect and distinguish multiple destination channels
- require destination selection before accepting a URL-list batch
- process many URLs independently without duplicate uploads
- monitor multiple configured source channels continuously
- acquire, remix, validate, and upload through durable jobs
- recover render/upload work after a restart
- publish immediately as public or upload privately with `publishAt` for scheduled public release
- apply metadata, thumbnail, and playlist settings
- delete downloaded sources only after confirmed successful processing
- preserve history after media cleanup
- start, pause, drain, update, disable, and emergency-stop by channel and pipeline
- show queue, progress, errors, quota, credentials, schedules, and history in one dashboard
- keep secrets encrypted and external actions auditable

## 18. Official references

- [YouTube push notifications](https://developers.google.com/youtube/v3/guides/push_notifications)
- [Uploads-playlist retrieval](https://developers.google.com/youtube/v3/docs/playlistItems/list)
- [YouTube OAuth](https://developers.google.com/youtube/v3/guides/authentication)
- [Video upload API](https://developers.google.com/youtube/v3/docs/videos/insert)
- [Resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [Video processing and scheduling fields](https://developers.google.com/youtube/v3/docs/videos)
- [Quota and compliance audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits)
- [YouTube API Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- [Required minimum functionality](https://developers.google.com/youtube/terms/required-minimum-functionality)
