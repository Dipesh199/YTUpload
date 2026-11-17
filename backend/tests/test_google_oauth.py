import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from automation.database import Base
from automation.models import (
    AutomationEvent,
    DestinationChannel,
    GoogleConnection,
    OAuthAuthorizationState,
    OAuthChannelCandidate,
    PublishJob,
    UrlBatch,
    utcnow,
)
from automation.oauth import (
    OAuthFlowError,
    adopt_oauth_candidate,
    discard_oauth_candidate,
    _decrypt,
    _credentials,
    _client_config,
    _encrypt,
    _state_hash,
    _validated_granted_scopes,
    cleanup_expired_oauth_candidates,
    complete_google_oauth,
    start_google_oauth,
)
from automation.schemas import BatchCreate, BatchSettings
from automation.service import create_batch, list_channels


class GoogleOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.credentials_path = Path(self.temp_dir.name) / "client.json"
        self.credentials_path.write_text(json.dumps({
            "web": {
                "client_id": "test-client.apps.googleusercontent.com",
                "client_secret": "test-client-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8000/api/automation/oauth/google/callback"],
            }
        }), encoding="utf-8")
        self.previous_credentials_file = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRETS_FILE")
        os.environ["GOOGLE_OAUTH_CLIENT_SECRETS_FILE"] = str(self.credentials_path)
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.channel = DestinationChannel(
            display_name="OAuth destination",
            youtube_channel_id="UCtestdestination00000001",
            oauth_status="not_connected",
            operational_state="paused",
        )
        self.session.add(self.channel)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        if self.previous_credentials_file is None:
            os.environ.pop("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", None)
        else:
            os.environ["GOOGLE_OAUTH_CLIENT_SECRETS_FILE"] = self.previous_credentials_file
        self.temp_dir.cleanup()

    def test_tokens_are_encrypted_with_deployment_secret_material(self):
        ciphertext = _encrypt("refresh-token-value")
        self.assertNotIn("refresh-token-value", ciphertext)
        self.assertEqual("refresh-token-value", _decrypt(ciphertext))

    def test_google_credentials_accept_timezone_aware_database_expiry(self):
        connection = GoogleConnection(
            destination_channel_id=self.channel.id,
            authorized_channel_id=self.channel.youtube_channel_id,
            channel_title="Expiry test",
            access_token_ciphertext=_encrypt("fresh-access-token"),
            refresh_token_ciphertext=_encrypt("refresh-token"),
            token_expiry=utcnow() + timedelta(hours=1),
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
            status="connected",
        )

        credentials = _credentials(connection, _client_config())

        self.assertTrue(credentials.valid)
        self.assertIsNone(credentials.expiry.tzinfo)

    def test_missing_required_youtube_scope_is_rejected(self):
        credentials = MagicMock()
        credentials.granted_scopes = ["openid", "email", "https://www.googleapis.com/auth/youtube.upload"]
        credentials.scopes = None
        with self.assertRaises(OAuthFlowError) as context:
            _validated_granted_scopes(credentials)
        self.assertEqual("required_scope_missing", context.exception.code)

    @patch("automation.oauth.Flow.from_client_config")
    def test_start_creates_one_time_state_without_persisting_raw_value(self, from_client_config):
        flow = MagicMock()
        flow.code_verifier = "test-pkce-verifier"
        flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/v2/auth?state=raw-state-only-in-url",
            "raw-state-only-in-url",
        )
        from_client_config.return_value = flow

        result = start_google_oauth(self.session, self.channel.id)

        state = self.session.scalar(select(OAuthAuthorizationState))
        self.assertTrue(result["authorization_url"].startswith("https://accounts.google.com/"))
        self.assertEqual(64, len(state.state_hash))
        self.assertNotEqual("raw-state-only-in-url", state.state_hash)
        self.assertNotIn("test-pkce-verifier", state.code_verifier_ciphertext)
        self.assertEqual("test-pkce-verifier", _decrypt(state.code_verifier_ciphertext))
        self.assertEqual("authorization_pending", self.channel.oauth_status)

    @patch("automation.oauth._channel_identity")
    @patch("automation.oauth.Flow.from_client_config")
    def test_callback_persists_only_exact_verified_channel(self, from_client_config, channel_identity):
        raw_state = "secure-one-time-state"
        self.session.add(OAuthAuthorizationState(
            destination_channel_id=self.channel.id,
            state_hash=_state_hash(raw_state),
            code_verifier_ciphertext=_encrypt("callback-pkce-verifier"),
            expires_at=utcnow() + timedelta(minutes=10),
        ))
        self.session.commit()
        credentials = MagicMock()
        credentials.token = "access-token"
        credentials.refresh_token = "refresh-token"
        credentials.expiry = utcnow() + timedelta(hours=1)
        credentials.scopes = None
        credentials.granted_scopes = [
            "openid",
            "email",
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]
        flow = MagicMock()
        flow.credentials = credentials
        from_client_config.return_value = flow
        channel_identity.return_value = ({
            "id": self.channel.youtube_channel_id,
            "snippet": {"title": "Verified channel", "thumbnails": {"default": {"url": "https://example.test/avatar.jpg"}}},
        }, {"sub": "google-subject", "email": "owner@example.test"})

        result = complete_google_oauth(self.session, raw_state, "authorization-code")

        connection = self.session.scalar(select(GoogleConnection))
        callback_kwargs = from_client_config.call_args.kwargs
        self.assertEqual(self.channel.id, result["channel_id"])
        self.assertEqual("connected", self.channel.oauth_status)
        self.assertEqual("callback-pkce-verifier", callback_kwargs["code_verifier"])
        self.assertFalse(callback_kwargs["autogenerate_code_verifier"])
        self.assertEqual(self.channel.youtube_channel_id, connection.authorized_channel_id)
        self.assertNotEqual("refresh-token", connection.refresh_token_ciphertext)
        self.assertEqual("refresh-token", _decrypt(connection.refresh_token_ciphertext))

    @patch("automation.oauth._channel_identity")
    @patch("automation.oauth.Flow.from_client_config")
    def test_callback_discovers_different_youtube_channel_for_confirmation(self, from_client_config, channel_identity):
        raw_state = "mismatch-state"
        self.session.add(OAuthAuthorizationState(
            destination_channel_id=self.channel.id,
            state_hash=_state_hash(raw_state),
            code_verifier_ciphertext=_encrypt("callback-pkce-verifier"),
            expires_at=utcnow() + timedelta(minutes=10),
        ))
        self.session.commit()
        flow = MagicMock()
        flow.credentials = MagicMock()
        flow.credentials.token = "mismatch-access-token"
        flow.credentials.refresh_token = "mismatch-refresh-token"
        flow.credentials.expiry = utcnow() + timedelta(hours=1)
        flow.credentials.granted_scopes = [
            "openid",
            "email",
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]
        flow.credentials.scopes = None
        from_client_config.return_value = flow
        channel_identity.return_value = (
            {"id": "UCdifferentchannel0000000", "snippet": {"title": "Wrong channel"}},
            {"email": "owner@example.test"},
        )

        result = complete_google_oauth(self.session, raw_state, "authorization-code")

        candidate = self.session.scalar(select(OAuthChannelCandidate))
        self.assertEqual("pending_adoption", result["status"])
        self.assertEqual("channel_mismatch", self.channel.oauth_status)
        self.assertIsNone(self.session.scalar(select(GoogleConnection)))
        self.assertEqual("UCdifferentchannel0000000", candidate.authorized_channel_id)
        self.assertEqual("Wrong channel", candidate.channel_title)
        self.assertNotIn("mismatch-refresh-token", candidate.refresh_token_ciphertext)
        event = self.session.scalar(select(AutomationEvent).where(
            AutomationEvent.event_type == "destination_channel.oauth_channel_mismatch"
        ))
        self.assertEqual("UCdifferentchannel0000000", event.details["authorized_channel_id"])

        self.session.delete(candidate)
        self.session.commit()
        self.session.expire(self.channel, ["pending_oauth_candidate"])
        channel_view = list_channels(self.session)[0]
        self.assertEqual("Wrong channel", channel_view["last_oauth_mismatch"]["channel_title"])
        self.assertEqual("UCdifferentchannel0000000", channel_view["last_oauth_mismatch"]["authorized_channel_id"])

    def _add_candidate(self, authorized_channel_id="UCadoptedchannel000000001"):
        candidate = OAuthChannelCandidate(
            destination_channel_id=self.channel.id,
            google_subject="subject",
            google_email="owner@example.test",
            authorized_channel_id=authorized_channel_id,
            channel_title="Authorized Brand Channel",
            channel_thumbnail_url="https://example.test/brand.jpg",
            access_token_ciphertext=_encrypt("candidate-access-token"),
            refresh_token_ciphertext=_encrypt("candidate-refresh-token"),
            token_expiry=utcnow() + timedelta(hours=1),
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.force-ssl",
            ],
            expires_at=utcnow() + timedelta(hours=1),
        )
        self.channel.oauth_status = "channel_mismatch"
        self.session.add(candidate)
        self.session.commit()
        return candidate

    def test_explicit_adoption_updates_identity_unblocks_jobs_and_preserves_batches(self):
        batch = create_batch(self.session, BatchCreate(
            destination_channel_id=self.channel.id,
            name="Existing batch",
            urls="https://youtu.be/dQw4w9WgXcQ",
            settings=BatchSettings(template_id="long-form-beat-remix"),
        ))
        batch_id = batch["id"]
        destination_id = self.channel.id
        candidate = self._add_candidate()

        result = adopt_oauth_candidate(self.session, self.channel.id, candidate.id)

        persisted_batch = self.session.get(UrlBatch, batch_id)
        publish_job = self.session.scalar(select(PublishJob))
        connection = self.session.scalar(select(GoogleConnection))
        self.assertEqual("connected", result["status"])
        self.assertEqual(destination_id, self.channel.id)
        self.assertEqual(destination_id, persisted_batch.destination_channel_id)
        self.assertEqual("UCadoptedchannel000000001", self.channel.youtube_channel_id)
        self.assertEqual("waiting_for_upload", publish_job.state)
        self.assertEqual("connected", connection.status)
        self.assertEqual("candidate-refresh-token", _decrypt(connection.refresh_token_ciphertext))
        self.assertIsNone(self.session.scalar(select(OAuthChannelCandidate)))
        adopted = self.session.scalar(select(AutomationEvent).where(
            AutomationEvent.event_type == "destination_channel.oauth_channel_adopted"
        ))
        self.assertEqual(1, adopted.details["publish_jobs_unblocked"])

    def test_adoption_rejects_channel_attached_to_another_destination(self):
        duplicate = DestinationChannel(
            display_name="Existing destination",
            youtube_channel_id="UCduplicatechannel00000001",
            oauth_status="not_connected",
            operational_state="paused",
        )
        self.session.add(duplicate)
        self.session.commit()
        candidate = self._add_candidate(duplicate.youtube_channel_id)

        with self.assertRaises(HTTPException) as context:
            adopt_oauth_candidate(self.session, self.channel.id, candidate.id)

        self.assertEqual(409, context.exception.status_code)
        self.assertEqual("UCtestdestination00000001", self.channel.youtube_channel_id)
        self.assertIsNotNone(self.session.get(OAuthChannelCandidate, candidate.id))

    def test_pending_candidate_survives_session_restart(self):
        candidate = self._add_candidate()
        candidate_id = candidate.id
        self.session.close()
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

        restored = self.session.get(OAuthChannelCandidate, candidate_id)

        self.assertEqual("Authorized Brand Channel", restored.channel_title)
        self.assertEqual("candidate-refresh-token", _decrypt(restored.refresh_token_ciphertext))

    @patch("automation.oauth.requests.post")
    def test_rejected_candidate_is_revoked_and_removed(self, revoke):
        revoke.return_value.status_code = 200
        candidate = self._add_candidate()

        result = discard_oauth_candidate(self.session, self.channel.id, candidate.id)

        self.assertEqual("not_connected", result["status"])
        self.assertEqual("revoked", result["revocation_status"])
        self.assertIsNone(self.session.get(OAuthChannelCandidate, candidate.id))
        revoke.assert_called_once()

    @patch("automation.oauth.requests.post")
    def test_abandoned_candidate_is_removed_after_expiry(self, revoke):
        revoke.return_value.status_code = 200
        candidate = self._add_candidate()
        candidate.expires_at = utcnow() - timedelta(seconds=1)
        self.session.commit()

        removed = cleanup_expired_oauth_candidates(self.session)

        self.assertEqual(1, removed)
        self.assertEqual("not_connected", self.channel.oauth_status)
        self.assertIsNone(self.session.get(OAuthChannelCandidate, candidate.id))
        revoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
