import os
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from automation.database import Base
from automation.models import (
    BatchItem,
    DestinationChannel,
    GoogleConnection,
    MediaAsset,
    OAuthAuthorizationState,
    PipelineVersion,
    PublishJob,
    RenderJob,
    SourceFeed,
    UrlBatch,
    utcnow,
)
from automation.publisher import YouTubePublisher
from automation.schemas import AutomationEditRequest, BatchCreate, BatchScheduleRequest, BatchSettings, ChannelCreate, SourceFeedCreate
from automation.service import (
    archive_channel,
    create_batch,
    create_channel,
    create_source_feed,
    list_channels,
    preview_urls,
    save_automation_edit,
    schedule_remaining_batch_publications,
    set_batch_processing,
    sync_source_feed,
)
from automation.source_feeds import DiscoveredChannel, DiscoveredVideo, normalize_source_channel_url
from automation.worker import AutomationWorker, retry_render_job


class AutomationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.Session()
        self.channel = create_channel(self.session, ChannelCreate(
            display_name="Test destination",
            youtube_channel_id="UCtestdestination00000001",
            timezone="Europe/Berlin",
            daily_upload_limit=4,
        ))

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_batch_creation_persists_immutable_job_foundation(self):
        video_url = "https://youtu.be/dQw4w9WgXcQ"
        preview = preview_urls(
            self.session,
            self.channel["id"],
            f"{video_url}\n{video_url}\nhttps://example.com/not-youtube",
        )
        self.assertEqual({"accepted": 1, "duplicate": 1, "invalid": 1}, preview["counts"])

        result = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Durable test batch",
            urls=f"{video_url}\n{video_url}\nhttps://example.com/not-youtube",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        ))

        self.assertEqual("processing", result["state"])
        self.assertEqual(3, len(result["items"]))
        self.assertEqual(1, len(self.session.scalars(select(UrlBatch)).all()))
        self.assertEqual(1, len(self.session.scalars(select(RenderJob)).all()))
        publish_job = self.session.scalar(select(PublishJob))
        self.assertEqual("blocked_not_connected", publish_job.state)

        second_preview = preview_urls(self.session, self.channel["id"], video_url)
        self.assertEqual(1, second_preview["counts"]["duplicate"])
        accepted = self.session.scalar(select(BatchItem).where(BatchItem.validation_status == "accepted"))
        self.assertEqual("dQw4w9WgXcQ", accepted.source_video_id)

    def test_scheduled_visibility_requires_a_publication_time(self):
        with self.assertRaises(ValidationError):
            BatchSettings(template_id="long-form-beat-remix", visibility="scheduled")

    def test_non_scheduled_visibility_is_normalized_to_public(self):
        result = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Public-only policy",
            urls="https://youtu.be/dQw4w9WgXcQ",
            settings=BatchSettings(template_id="long-form-beat-remix", visibility="private"),
        ))

        publish_job = self.session.scalar(select(PublishJob))
        self.assertEqual("public", result["settings_snapshot"]["visibility"])
        self.assertIsNone(result["settings_snapshot"]["scheduled_for"])
        self.assertEqual("public", publish_job.visibility)

    def test_scheduled_batch_assigns_distinct_publication_slots(self):
        start = utcnow() + timedelta(hours=2)

        result = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Thirty minute schedule",
            urls="\n".join([
                "https://youtu.be/AAAAAAAAAAA",
                "https://youtu.be/BBBBBBBBBBB",
                "https://youtu.be/CCCCCCCCCCC",
            ]),
            settings=BatchSettings(
                template_id="long-form-beat-remix",
                visibility="scheduled",
                scheduled_for=start,
                schedule_interval_minutes=30,
            ),
        ))

        slots = [item["publish_job"]["scheduled_for"] for item in result["items"]]
        self.assertEqual(3, len(slots))
        self.assertEqual(30 * 60, slots[1].timestamp() - slots[0].timestamp())
        self.assertEqual(30 * 60, slots[2].timestamp() - slots[1].timestamp())
        self.assertEqual(30, result["settings_snapshot"]["schedule_interval_minutes"])

    def test_pausing_batch_stops_next_render_claim_and_resume_continues(self):
        result = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Pause boundary",
            urls="https://youtu.be/AAAAAAAAAAA\nhttps://youtu.be/BBBBBBBBBBB",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        ))
        jobs = self.session.scalars(select(RenderJob).order_by(RenderJob.created_at)).all()
        current, waiting = jobs
        current.state = "rendering"
        current.progress = 45
        current.batch_item.state = "rendering"
        self.session.commit()

        paused = set_batch_processing(self.session, result["id"], "paused")
        worker = AutomationWorker(Path("."), Path("."), Path("."))
        with patch("automation.worker.SessionLocal", self.Session):
            paused_claim = worker._claim_next_job()

        self.session.expire_all()
        self.assertEqual("paused", paused["processing_state"])
        self.assertEqual("rendering", self.session.get(RenderJob, current.id).state)
        self.assertIsNone(paused_claim)
        self.assertEqual("waiting_for_source", self.session.get(RenderJob, waiting.id).state)

        resumed = set_batch_processing(self.session, result["id"], "running")
        with patch("automation.worker.SessionLocal", self.Session):
            resumed_claim = worker._claim_next_job()

        self.assertEqual("running", resumed["processing_state"])
        self.assertEqual(waiting.id, resumed_claim)

    def test_existing_public_batch_can_schedule_only_remaining_videos(self):
        result = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Adopt scheduled publishing",
            urls="https://youtu.be/AAAAAAAAAAA\nhttps://youtu.be/BBBBBBBBBBB",
            settings=BatchSettings(template_id="long-form-beat-remix", visibility="public"),
        ))
        jobs = self.session.scalars(select(PublishJob).order_by(PublishJob.created_at)).all()
        uploaded, pending = jobs
        uploaded.youtube_video_id = "already-uploaded"
        uploaded.state = "youtube_processing"
        pending.state = "waiting_for_upload"
        self.session.commit()

        changed = schedule_remaining_batch_publications(
            self.session,
            result["id"],
            BatchScheduleRequest(schedule_interval_minutes=30),
        )

        self.assertEqual("public", uploaded.visibility)
        self.assertIsNone(uploaded.scheduled_for)
        self.assertEqual("scheduled", pending.visibility)
        self.assertIsNotNone(pending.scheduled_for)
        self.assertEqual("scheduled", changed["settings_snapshot"]["visibility"])
        self.assertEqual(30, changed["settings_snapshot"]["schedule_interval_minutes"])

    def test_failed_render_can_be_retried_idempotently(self):
        create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Retry test",
            urls="https://youtu.be/jNQXAC9IVRw",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        ))
        job = self.session.scalar(select(RenderJob))
        job.state = "render_failed"
        job.error_message = "test failure"
        job.batch_item.state = "failed"
        self.session.commit()

        result = retry_render_job(self.session, job.id)

        self.assertEqual("retry_requested", result["state"])
        self.assertEqual("queued", job.batch_item.state)
        self.assertIsNone(job.error_message)

    def test_editor_save_creates_immutable_pipeline_and_requeues_render(self):
        create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Manual edit test",
            urls="https://youtu.be/jNQXAC9IVRw",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        ))
        job = self.session.scalar(select(RenderJob))
        original_version_id = job.pipeline_version_id
        job.state = "completed"
        job.progress = 100
        job.batch_item.state = "rendered"
        source = self.session.scalar(select(MediaAsset).where(MediaAsset.kind == "source"))
        source.state = "ready"
        source.storage_path = "AUTO-SRC-test-jNQXAC9IVRw.mp4"
        self.session.commit()

        result = save_automation_edit(self.session, job.id, AutomationEditRequest(settings={
            "name": "Queue edit",
            "output_profile": {"id": "youtube-1080p"},
            "mirror": {"horizontal": True},
        }))

        self.assertEqual("edit_requested", result["state"])
        self.assertNotEqual(original_version_id, result["pipeline_version_id"])
        self.assertEqual(2, len(self.session.scalars(select(PipelineVersion)).all()))
        self.assertEqual("manual_edit_requested", job.error_code)
        self.assertEqual("queued", job.batch_item.state)

    def test_channel_removal_preserves_batches_and_invalidates_pending_oauth(self):
        result = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel["id"],
            name="Preserved history",
            urls="https://youtu.be/dQw4w9WgXcQ",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        ))
        oauth_state = OAuthAuthorizationState(
            destination_channel_id=self.channel["id"],
            state_hash="a" * 64,
            expires_at=utcnow() + timedelta(minutes=10),
        )
        destination = self.session.get(DestinationChannel, self.channel["id"])
        destination.oauth_status = "authorization_pending"
        self.session.add(oauth_state)
        self.session.commit()

        removed = archive_channel(self.session, destination.id)

        self.assertEqual("removed", removed["status"])
        self.assertEqual(1, removed["preserved_batch_count"])
        self.assertEqual(result["id"], self.session.get(UrlBatch, result["id"]).id)
        self.assertEqual("disabled", destination.operational_state)
        self.assertIsNotNone(destination.archived_at)
        self.assertEqual([], list_channels(self.session))
        self.assertIsNone(self.session.get(OAuthAuthorizationState, oauth_state.id))

    def test_connected_channel_must_be_disconnected_before_removal(self):
        connection = GoogleConnection(
            destination_channel_id=self.channel["id"],
            authorized_channel_id="UCtestdestination00000001",
            channel_title="Connected destination",
            refresh_token_ciphertext="encrypted-placeholder",
            scopes=[],
            status="connected",
        )
        self.session.add(connection)
        self.session.commit()

        with self.assertRaises(HTTPException) as context:
            archive_channel(self.session, self.channel["id"])

        self.assertEqual(409, context.exception.status_code)
        self.assertIsNone(self.session.get(DestinationChannel, self.channel["id"]).archived_at)

    def test_source_channel_import_queues_every_new_video_and_resync_skips_duplicates(self):
        discovery = DiscoveredChannel(
            channel_id="UCsourcechannel000000001",
            title="Source channel",
            videos=[
                DiscoveredVideo("AAAAAAAAAAA", "First video", "https://www.youtube.com/watch?v=AAAAAAAAAAA", "https://i.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"),
                DiscoveredVideo("BBBBBBBBBBB", "Second video", "https://www.youtube.com/watch?v=BBBBBBBBBBB", "https://i.ytimg.com/vi/BBBBBBBBBBB/hqdefault.jpg"),
            ],
        )
        payload = SourceFeedCreate(
            destination_channel_id=self.channel["id"],
            name="Complete source import",
            source_channel_url="youtube.com/@sourcechannel",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        )

        result = create_source_feed(self.session, payload, discoverer=lambda _url: discovery)

        self.assertEqual("active", result["feed"]["state"])
        self.assertEqual(2, result["feed"]["discovered_count"])
        self.assertEqual(2, result["feed"]["queued_count"])
        self.assertEqual(2, result["batch"]["accepted_count"])
        self.assertEqual(["First video", "Second video"], [item["source_title"] for item in result["batch"]["items"]])
        self.assertEqual(1, len(self.session.scalars(select(SourceFeed)).all()))

        repeated = sync_source_feed(self.session, result["feed"]["id"], discoverer=lambda _url: discovery)

        self.assertIsNone(repeated["batch"])
        self.assertEqual(0, repeated["feed"]["queued_count"])
        self.assertEqual(2, repeated["feed"]["duplicate_count"])
        self.assertEqual(1, len(self.session.scalars(select(UrlBatch)).all()))

    def test_source_channel_url_normalization_rejects_video_links(self):
        self.assertEqual(
            "https://www.youtube.com/@source",
            normalize_source_channel_url("youtube.com/@source"),
        )
        with self.assertRaises(ValueError):
            normalize_source_channel_url("https://www.youtube.com/watch?v=AAAAAAAAAAA")

    def test_due_rendered_upload_is_claimed_before_processing_poll_and_reserves_cadence(self):
        destination = self.session.get(DestinationChannel, self.channel["id"])
        destination.oauth_status = "connected"
        destination.operational_state = "active"
        destination.next_publish_at = utcnow() - timedelta(minutes=1)
        destination.upload_interval_minutes = 15
        self.session.commit()
        create_batch(self.session, BatchCreate(
            destination_channel_id=destination.id,
            name="Independent handoff",
            urls="https://youtu.be/AAAAAAAAAAA\nhttps://youtu.be/BBBBBBBBBBB",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        ))
        jobs = self.session.scalars(select(PublishJob).order_by(PublishJob.created_at)).all()
        processing, waiting = jobs
        processing.state = "youtube_processing"
        processing.youtube_video_id = "published0001"
        waiting.state = "waiting_for_upload"
        self.session.commit()
        before = utcnow()

        publisher = YouTubePublisher(Path("."), Path("."))
        with patch("automation.publisher.SessionLocal", self.Session):
            claimed_id = publisher._claim_next_job()

        self.session.expire_all()
        self.assertEqual(waiting.id, claimed_id)
        self.assertEqual("uploading", self.session.get(PublishJob, waiting.id).state)
        reserved = self.session.get(DestinationChannel, destination.id).next_publish_at
        self.assertGreaterEqual(reserved.timestamp(), (before + timedelta(minutes=14, seconds=50)).timestamp())


if __name__ == "__main__":
    unittest.main()
