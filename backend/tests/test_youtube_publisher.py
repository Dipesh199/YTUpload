import os
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from automation.database import Base
from automation.models import DestinationChannel, GoogleConnection, MediaAsset, PublishJob, UploadSession, utcnow
from automation.oauth import _decrypt, _encrypt
from automation.publisher import YouTubePublisher, make_channel_videos_public, retry_publish_job
from automation.schemas import BatchCreate, BatchSettings, ChannelCreate
from automation.service import create_batch, create_channel


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.content = b""

    def json(self):
        return self._payload


class StartSessionClient:
    def __init__(self):
        self.call = None

    def post(self, url, **kwargs):
        self.call = (url, kwargs)
        return FakeResponse(200, headers={"Location": "https://upload.example.test/private-session"})


class ResumeClient:
    def __init__(self):
        self.puts = []

    def put(self, url, **kwargs):
        self.puts.append((url, kwargs))
        if len(self.puts) == 1:
            return FakeResponse(308, headers={"Range": "bytes=0-3"})
        return FakeResponse(201, {"id": "youtube-video-1"})


class ProcessingClient:
    def get(self, _url, **_kwargs):
        return FakeResponse(200, {
            "items": [{
                "status": {"uploadStatus": "processed"},
                "processingDetails": {"processingStatus": "succeeded"},
            }]
        })


class VisibilityClient:
    def __init__(self, privacy_status="private"):
        self.puts = []
        self.privacy_status = privacy_status

    def get(self, _url, **kwargs):
        video_id = kwargs.get("params", {}).get("id", "")
        status = {
            "privacyStatus": self.privacy_status,
            "embeddable": True,
            "license": "youtube",
            "publicStatsViewable": True,
        }
        if "scheduled" in video_id and self.privacy_status == "private":
            status["publishAt"] = (utcnow() + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        return FakeResponse(200, {
            "items": [{
                "id": video_id,
                "status": status,
            }]
        })

    def put(self, url, **kwargs):
        self.puts.append((url, kwargs))
        return FakeResponse(200, {"id": kwargs["json"]["id"]})


class YouTubePublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.input_dir = root / "input"
        self.output_dir = root / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        self.credentials_path = root / "client.json"
        self.credentials_path.write_text(json.dumps({"web": {
            "client_id": "publisher-test.apps.googleusercontent.com",
            "client_secret": "publisher-test-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8000/api/automation/oauth/google/callback"],
        }}), encoding="utf-8")
        self.previous_credentials_file = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRETS_FILE")
        os.environ["GOOGLE_OAUTH_CLIENT_SECRETS_FILE"] = str(self.credentials_path)
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.factory()
        channel_data = create_channel(self.session, ChannelCreate(
            display_name="Publisher destination",
            youtube_channel_id="UCpublisherdestination01",
        ))
        self.channel = self.session.get(DestinationChannel, channel_data["id"])
        self.channel.oauth_status = "connected"
        self.channel.operational_state = "active"
        self.channel.next_publish_at = None
        self.connection = GoogleConnection(
            destination_channel_id=self.channel.id,
            authorized_channel_id=self.channel.youtube_channel_id,
            channel_title=self.channel.display_name,
            refresh_token_ciphertext=_encrypt("publisher-refresh-token"),
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
            status="connected",
        )
        self.session.add(self.connection)
        result = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel.id,
            name="Publish test",
            urls="https://youtu.be/dQw4w9WgXcQ",
            settings=BatchSettings(
                template_id="long-form-beat-remix",
                thumbnail_mode="none",
                delete_source_after_success=False,
            ),
        ))
        self.job = self.session.scalar(select(PublishJob))
        self.job.state = "waiting_for_upload"
        self.job.batch_item.state = "rendered"
        self.job.batch_item.render_job.state = "completed"
        self.job.batch_item.render_job.progress = 100
        self.video_path = self.output_dir / "altered.mp4"
        self.video_path.write_bytes(b"abcdefgh")
        self.export = MediaAsset(
            batch_item_id=self.job.batch_item_id,
            kind="export",
            state="ready",
            storage_path=self.video_path.name,
            size_bytes=self.video_path.stat().st_size,
            metadata_snapshot={"title": "Publisher test title", "description": "Description", "tags": ["one"]},
        )
        self.session.add(self.export)
        self.session.commit()
        self.publisher = YouTubePublisher(self.input_dir, self.output_dir)
        self.session_factory_patch = patch("automation.publisher.SessionLocal", self.factory)
        self.session_factory_patch.start()

    def tearDown(self):
        self.session_factory_patch.stop()
        self.session.close()
        self.engine.dispose()
        if self.previous_credentials_file is None:
            os.environ.pop("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", None)
        else:
            os.environ["GOOGLE_OAUTH_CLIENT_SECRETS_FILE"] = self.previous_credentials_file
        self.temp_dir.cleanup()

    def test_resumable_session_url_is_encrypted_and_survives_reload(self):
        session_url = "https://upload.example.test/sensitive-session"

        self.publisher._persist_session(self.job.id, session_url)
        self.session.expire_all()
        upload = self.session.scalar(select(UploadSession))
        reloaded = self.publisher._spec(self.job.id)

        self.assertNotIn(session_url, upload.session_url_ciphertext)
        self.assertEqual(session_url, _decrypt(upload.session_url_ciphertext))
        self.assertEqual(session_url, reloaded["session_url"])

    def test_public_upload_session_uses_frozen_metadata_and_notification_setting(self):
        client = StartSessionClient()
        spec = self.publisher._spec(self.job.id)

        session_url = self.publisher._start_session(client, spec)

        _url, call = client.call
        self.assertEqual("https://upload.example.test/private-session", session_url)
        self.assertEqual("public", call["json"]["status"]["privacyStatus"])
        self.assertEqual("Publisher test title", call["json"]["snippet"]["title"])
        self.assertEqual("false", call["params"]["notifySubscribers"])

    def test_scheduled_upload_is_private_with_public_publish_time(self):
        scheduled_for = utcnow() + timedelta(hours=2)
        self.job.visibility = "scheduled"
        self.job.scheduled_for = scheduled_for
        self.session.commit()
        client = StartSessionClient()
        spec = self.publisher._spec(self.job.id)

        self.publisher._start_session(client, spec)

        _url, call = client.call
        self.assertEqual("private", call["json"]["status"]["privacyStatus"])
        self.assertEqual(scheduled_for.isoformat().replace("+00:00", "Z"), call["json"]["status"]["publishAt"])

    def test_resume_probes_server_offset_and_persists_video_id_before_processing(self):
        self.publisher._persist_session(self.job.id, "https://upload.example.test/resume-session")
        client = ResumeClient()
        spec = self.publisher._spec(self.job.id)

        with patch.object(self.publisher, "_poll_processing"):
            self.publisher._upload(client, spec)

        self.session.expire_all()
        job = self.session.get(PublishJob, self.job.id)
        self.assertEqual(b"efgh", client.puts[1][1]["data"])
        self.assertEqual("bytes 4-7/8", client.puts[1][1]["headers"]["Content-Range"])
        self.assertEqual("youtube-video-1", job.youtube_video_id)
        self.assertEqual("youtube_processing", job.state)
        self.assertIsNone(job.upload_session.session_url_ciphertext)

    def test_processing_success_marks_job_published_without_reupload(self):
        self.job.youtube_video_id = "youtube-video-1"
        self.job.state = "youtube_processing"
        self.job.batch_item.state = "youtube_processing"
        self.session.commit()
        spec = self.publisher._spec(self.job.id)

        self.publisher._poll_processing(ProcessingClient(), spec)

        self.session.expire_all()
        job = self.session.get(PublishJob, self.job.id)
        self.assertEqual("published", job.state)
        self.assertEqual(100, job.progress)
        self.assertEqual("published", job.batch_item.state)
        self.assertEqual("published", job.batch_item.batch.state)

    def test_processing_success_keeps_future_release_as_scheduled(self):
        self.job.youtube_video_id = "youtube-video-scheduled"
        self.job.visibility = "scheduled"
        self.job.scheduled_for = utcnow() + timedelta(hours=2)
        self.job.state = "youtube_processing"
        self.job.batch_item.state = "youtube_processing"
        self.session.commit()
        spec = self.publisher._spec(self.job.id)

        self.publisher._poll_processing(ProcessingClient(), spec)

        self.session.expire_all()
        job = self.session.get(PublishJob, self.job.id)
        self.assertEqual("scheduled", job.state)
        self.assertEqual("scheduled", job.batch_item.state)
        self.assertEqual("scheduled", job.batch_item.batch.state)

    def test_shorts_skip_custom_thumbnail_even_for_existing_source_thumbnail_setting(self):
        client = StartSessionClient()
        spec = {
            "settings": {"output_profile": "youtube-shorts", "thumbnail_mode": "source"},
            "thumbnail_url": "https://example.test/source-thumbnail.jpg",
        }

        with patch("automation.publisher.requests.get") as download:
            self.publisher._apply_thumbnail(client, spec, "youtube-short-1")

        download.assert_not_called()
        self.assertIsNone(client.call)

    def test_long_form_still_uploads_source_thumbnail(self):
        client = StartSessionClient()
        spec = {
            "settings": {"output_profile": "youtube-1080p", "thumbnail_mode": "source"},
            "thumbnail_url": "https://example.test/source-thumbnail.jpg",
        }
        thumbnail = FakeResponse(200, headers={"Content-Type": "image/jpeg"})
        thumbnail.content = b"thumbnail"

        with patch("automation.publisher.requests.get", return_value=thumbnail):
            self.publisher._apply_thumbnail(client, spec, "youtube-video-1")

        self.assertIn("/thumbnails/set", client.call[0])

    def test_shorts_settings_are_normalized_to_no_custom_thumbnail(self):
        settings = BatchSettings(
            template_id="long-form-beat-remix",
            output_profile="youtube-shorts",
            thumbnail_mode="source",
        )

        self.assertEqual("none", settings.thumbnail_mode)

    def test_due_waiting_upload_is_claimed_before_processing_poll(self):
        create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel.id,
            name="Processing priority",
            urls="https://youtu.be/jNQXAC9IVRw",
            settings=BatchSettings(
                template_id="long-form-beat-remix",
                thumbnail_mode="none",
                delete_source_after_success=False,
            ),
        ))
        processing = self.session.scalar(select(PublishJob).where(PublishJob.id != self.job.id))
        processing.state = "youtube_processing"
        processing.youtube_video_id = "youtube-video-processing"
        processing.batch_item.state = "youtube_processing"
        self.session.commit()

        claimed = self.publisher._claim_next_job()

        self.session.expire_all()
        self.assertEqual(self.job.id, claimed)
        self.assertEqual("uploading", self.job.state)
        self.assertEqual("youtube_processing", processing.state)

    def test_paused_batch_does_not_block_completed_video_upload(self):
        self.job.batch_item.batch.processing_state = "paused"
        self.session.commit()

        claimed = self.publisher._claim_next_job()

        self.session.expire_all()
        self.assertEqual(self.job.id, claimed)
        self.assertEqual("uploading", self.session.get(PublishJob, self.job.id).state)

    def test_stale_schedule_is_moved_to_a_future_thirty_minute_slot(self):
        self.job.visibility = "scheduled"
        self.job.scheduled_for = utcnow() - timedelta(minutes=5)
        self.job.batch_item.batch.settings_snapshot = {
            **self.job.batch_item.batch.settings_snapshot,
            "schedule_interval_minutes": 30,
        }
        before = utcnow()
        self.session.commit()

        claimed = self.publisher._claim_next_job()

        self.session.expire_all()
        scheduled_for = self.session.get(PublishJob, self.job.id).scheduled_for
        self.assertEqual(self.job.id, claimed)
        self.assertGreaterEqual(scheduled_for.timestamp(), (before + timedelta(minutes=29, seconds=50)).timestamp())

    def test_scheduled_upload_ignores_upload_start_cooldown(self):
        cooldown = utcnow() + timedelta(minutes=15)
        self.channel.next_publish_at = cooldown
        self.job.visibility = "scheduled"
        self.job.scheduled_for = utcnow() + timedelta(hours=2)
        self.job.batch_item.batch.settings_snapshot = {
            **self.job.batch_item.batch.settings_snapshot,
            "schedule_interval_minutes": 30,
        }
        self.session.commit()

        claimed = self.publisher._claim_next_job()

        self.session.expire_all()
        self.assertEqual(self.job.id, claimed)
        self.assertEqual("uploading", self.session.get(PublishJob, self.job.id).state)
        self.assertEqual(cooldown.timestamp(), self.session.get(DestinationChannel, self.channel.id).next_publish_at.timestamp())

    def test_failed_post_publish_work_can_retry_without_creating_another_video(self):
        self.job.youtube_video_id = "youtube-video-1"
        self.job.state = "post_publish_failed"
        self.job.error_code = "thumbnail_upload_failed"
        self.job.batch_item.state = "failed"
        self.session.commit()

        result = retry_publish_job(self.session, self.job.id)

        self.assertEqual("youtube_processing", result["state"])
        self.assertEqual("youtube-video-1", self.job.youtube_video_id)
        self.assertEqual("youtube_processing", self.job.batch_item.state)

    def test_processing_failure_stops_at_five_total_attempts(self):
        self.job.youtube_video_id = "youtube-video-1"
        self.job.state = "youtube_processing"
        self.job.attempts = 4
        self.job.batch_item.state = "youtube_processing"
        self.session.commit()

        self.publisher._fail(self.job.id, "processing_check_failed", "temporary failure", True)

        self.session.expire_all()
        job = self.session.get(PublishJob, self.job.id)
        self.assertEqual(5, job.attempts)
        self.assertEqual("processing_failed", job.state)
        self.assertEqual("youtube-video-1", job.youtube_video_id)

    def test_manual_retry_is_rejected_after_five_attempts(self):
        self.job.youtube_video_id = "youtube-video-1"
        self.job.state = "processing_failed"
        self.job.attempts = 5
        self.job.batch_item.state = "failed"
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "maximum of 5 publish attempts"):
            retry_publish_job(self.session, self.job.id)

    def test_processing_checks_wait_before_reclaiming_the_same_video(self):
        self.job.youtube_video_id = "youtube-video-1"
        self.job.state = "youtube_processing"
        self.job.batch_item.state = "youtube_processing"
        self.session.commit()

        self.publisher._release_processing(self.job.id)

        self.session.expire_all()
        job = self.session.get(PublishJob, self.job.id)
        self.assertGreater(job.lease_expires_at.timestamp(), utcnow().timestamp())
        self.assertIsNone(self.publisher._claim_next_job())

    def test_existing_unscheduled_video_is_changed_to_public_on_youtube(self):
        self.job.youtube_video_id = "youtube-video-private"
        self.job.visibility = "private"
        self.job.state = "published"
        self.job.batch_item.state = "published"
        self.session.commit()
        client = VisibilityClient()

        with (
            patch("automation.publisher.youtube_authorized_session", return_value=client),
            patch.object(YouTubePublisher, "_verify_destination"),
        ):
            result = make_channel_videos_public(self.session, self.channel.id)

        self.session.expire_all()
        status = client.puts[0][1]["json"]["status"]
        self.assertEqual("public", status["privacyStatus"])
        self.assertTrue(status["embeddable"])
        self.assertEqual("youtube", status["license"])
        self.assertNotIn("publishAt", status)
        self.assertEqual("public", self.session.get(PublishJob, self.job.id).visibility)
        self.assertEqual(1, result["updated_on_youtube"])
        self.assertEqual(0, result["already_public"])
        self.assertEqual([], result["failures"])

    def test_existing_public_video_is_verified_without_rewriting_it(self):
        self.job.youtube_video_id = "youtube-video-public"
        self.job.visibility = "public"
        self.job.state = "published"
        self.job.batch_item.state = "published"
        self.session.commit()
        client = VisibilityClient(privacy_status="public")

        with (
            patch("automation.publisher.youtube_authorized_session", return_value=client),
            patch.object(YouTubePublisher, "_verify_destination"),
        ):
            result = make_channel_videos_public(self.session, self.channel.id)

        self.assertEqual([], client.puts)
        self.assertEqual(0, result["updated_on_youtube"])
        self.assertEqual(1, result["already_public"])
        self.assertEqual([], result["failures"])

    def test_scheduled_video_keeps_its_release_time_and_is_not_published_early(self):
        scheduled_for = utcnow() + timedelta(hours=2)
        self.job.youtube_video_id = "youtube-video-scheduled"
        self.job.visibility = "scheduled"
        self.job.scheduled_for = scheduled_for
        self.job.state = "scheduled"
        self.job.batch_item.state = "scheduled"
        self.session.commit()
        client = VisibilityClient()

        with (
            patch("automation.publisher.youtube_authorized_session", return_value=client),
            patch.object(YouTubePublisher, "_verify_destination"),
        ):
            result = make_channel_videos_public(self.session, self.channel.id)

        self.session.expire_all()
        job = self.session.get(PublishJob, self.job.id)
        self.assertEqual([], client.puts)
        self.assertEqual("scheduled", job.visibility)
        self.assertEqual(scheduled_for.replace(tzinfo=None), job.scheduled_for)
        self.assertEqual(1, result["scheduled_public_at_release"])

    def test_scheduled_video_already_public_is_reconciled_as_published(self):
        self.job.youtube_video_id = "youtube-video-scheduled"
        self.job.visibility = "scheduled"
        self.job.scheduled_for = utcnow() + timedelta(hours=2)
        self.job.state = "scheduled"
        self.job.batch_item.state = "scheduled"
        self.session.commit()
        client = VisibilityClient(privacy_status="public")

        with (
            patch("automation.publisher.youtube_authorized_session", return_value=client),
            patch.object(YouTubePublisher, "_verify_destination"),
        ):
            result = make_channel_videos_public(self.session, self.channel.id)

        self.session.expire_all()
        job = self.session.get(PublishJob, self.job.id)
        self.assertEqual([], client.puts)
        self.assertEqual("public", job.visibility)
        self.assertEqual("published", job.state)
        self.assertEqual("published", job.batch_item.state)
        self.assertEqual(1, result["scheduled_already_public"])


if __name__ == "__main__":
    unittest.main()
