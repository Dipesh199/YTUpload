from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from google.auth.transport.requests import AuthorizedSession, Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import (
    AutomationEvent,
    DestinationChannel,
    GoogleConnection,
    OAuthAuthorizationState,
    OAuthChannelCandidate,
    PublishJob,
    utcnow,
)


YOUTUBE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
REQUIRED_YOUTUBE_SCOPES = {
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
}
YOUTUBE_CHANNELS_URL = "https://youtube.googleapis.com/youtube/v3/channels"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
STATE_TTL_MINUTES = 10
CANDIDATE_TTL_MINUTES = 60

# Google may return equivalent identity aliases or previously granted scopes when
# include_granted_scopes is enabled. OAuthLib otherwise raises after a successful
# exchange. The exact YouTube permissions are checked explicitly below.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class OAuthFlowError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _client_config() -> dict[str, Any]:
    secrets_file = os.getenv("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", "").strip()
    if secrets_file:
        try:
            loaded = json.loads(Path(secrets_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OAuthFlowError("credentials_unreadable", "Google OAuth credentials could not be read") from error
        web = loaded.get("web") if isinstance(loaded, dict) else None
        if not isinstance(web, dict):
            raise OAuthFlowError("credentials_invalid", "Google OAuth credentials must be a Web application client")
        return {"web": web}

    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if not all((client_id, client_secret, redirect_uri)):
        raise OAuthFlowError("not_configured", "Google OAuth client credentials are not configured")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def oauth_configuration() -> dict[str, Any]:
    try:
        config = _client_config()["web"]
        redirect_uri = _redirect_uri(config)
        return {"configured": True, "redirect_uri": redirect_uri}
    except OAuthFlowError as error:
        return {"configured": False, "error": error.code, "redirect_uri": None}


def _redirect_uri(web_config: dict[str, Any]) -> str:
    override = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    redirect_uris = web_config.get("redirect_uris") or []
    redirect_uri = override or (redirect_uris[0] if redirect_uris else "")
    if not redirect_uri:
        raise OAuthFlowError("redirect_uri_missing", "Google OAuth redirect URI is not configured")
    return str(redirect_uri)


def _fernet(config: dict[str, Any] | None = None) -> Fernet:
    web = (config or _client_config())["web"]
    key_file = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY_FILE", "").strip()
    if key_file:
        try:
            encoded_key = Path(key_file).read_text(encoding="ascii").strip().encode("ascii")
            return Fernet(encoded_key)
        except (OSError, ValueError) as error:
            raise OAuthFlowError("encryption_key_invalid", "OAuth token encryption key could not be loaded") from error
    key_material = os.getenv("OAUTH_TOKEN_ENCRYPTION_SECRET", "").strip() or str(web["client_secret"])
    digest = hashlib.sha256(f"ytupload-google-oauth-v1:{key_material}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str, config: dict[str, Any] | None = None) -> str:
    return _fernet(config).encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str | None, config: dict[str, Any] | None = None) -> str | None:
    if not value:
        return None
    try:
        return _fernet(config).decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise OAuthFlowError("token_decryption_failed", "Stored Google authorization could not be decrypted") from error


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _credential_expiry(value: datetime | None) -> datetime | None:
    """google-auth compares credential expiry as a naive UTC datetime."""
    aware = _aware(value)
    return aware.astimezone(timezone.utc).replace(tzinfo=None) if aware else None


def connection_dict(connection: GoogleConnection | None) -> dict[str, Any] | None:
    if not connection:
        return None
    return {
        "status": connection.status,
        "google_email": connection.google_email,
        "authorized_channel_id": connection.authorized_channel_id,
        "channel_title": connection.channel_title,
        "channel_thumbnail_url": connection.channel_thumbnail_url,
        "token_expiry": connection.token_expiry,
        "scopes": connection.scopes,
        "last_verified_at": connection.last_verified_at,
        "connected_at": connection.created_at,
    }


def candidate_dict(candidate: OAuthChannelCandidate | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    return {
        "id": candidate.id,
        "google_email": candidate.google_email,
        "authorized_channel_id": candidate.authorized_channel_id,
        "channel_title": candidate.channel_title,
        "channel_thumbnail_url": candidate.channel_thumbnail_url,
        "expires_at": candidate.expires_at,
        "discovered_at": candidate.created_at,
    }


def _revoke_ciphertext_best_effort(
    refresh_token_ciphertext: str | None,
    access_token_ciphertext: str | None,
    config: dict[str, Any],
) -> str:
    try:
        token = _decrypt(refresh_token_ciphertext, config) or _decrypt(access_token_ciphertext, config)
        if not token:
            return "no_token"
        response = requests.post(GOOGLE_REVOKE_URL, data={"token": token}, timeout=30)
        return "revoked" if response.status_code == 200 else "already_invalid" if response.status_code == 400 else "failed"
    except (OAuthFlowError, requests.RequestException):
        return "failed"


def _remove_candidate(
    db: Session,
    channel: DestinationChannel,
    candidate: OAuthChannelCandidate,
    config: dict[str, Any],
    event_type: str,
) -> str:
    revocation_status = _revoke_ciphertext_best_effort(
        candidate.refresh_token_ciphertext,
        candidate.access_token_ciphertext,
        config,
    )
    db.delete(candidate)
    db.add(AutomationEvent(
        aggregate_type="destination_channel",
        aggregate_id=channel.id,
        event_type=event_type,
        from_state=channel.oauth_status,
        to_state=channel.oauth_status,
        details={
            "authorized_channel_id": candidate.authorized_channel_id,
            "authorized_channel_title": candidate.channel_title,
            "revocation_status": revocation_status,
        },
    ))
    return revocation_status


def start_google_oauth(db: Session, channel_id: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, channel_id)
    if not channel or channel.archived_at:
        raise HTTPException(404, "Destination channel not found")
    config = _client_config()
    redirect_uri = _redirect_uri(config["web"])
    now = utcnow()
    if channel.pending_oauth_candidate:
        _remove_candidate(
            db,
            channel,
            channel.pending_oauth_candidate,
            config,
            "destination_channel.oauth_candidate_abandoned",
        )
    db.execute(delete(OAuthAuthorizationState).where(OAuthAuthorizationState.expires_at < now))
    raw_state = secrets.token_urlsafe(48)
    state_record = OAuthAuthorizationState(
        destination_channel_id=channel.id,
        state_hash=_state_hash(raw_state),
        expires_at=now + timedelta(minutes=STATE_TTL_MINUTES),
    )
    db.add(state_record)
    db.add(AutomationEvent(
        aggregate_type="destination_channel",
        aggregate_id=channel.id,
        event_type="destination_channel.oauth_started",
        from_state=channel.oauth_status,
        to_state="authorization_pending",
        details={"expires_in_minutes": STATE_TTL_MINUTES},
    ))
    if not channel.google_connection or channel.google_connection.status != "connected":
        channel.oauth_status = "authorization_pending"
    flow = Flow.from_client_config(config, scopes=YOUTUBE_SCOPES, state=raw_state)
    flow.redirect_uri = redirect_uri
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent select_account",
    )
    if not flow.code_verifier:
        raise OAuthFlowError("pkce_verifier_missing", "Google OAuth PKCE verifier was not generated")
    state_record.code_verifier_ciphertext = _encrypt(flow.code_verifier, config)
    db.commit()
    return {"authorization_url": authorization_url, "expires_at": state_record.expires_at}


def _consume_state(db: Session, state: str) -> OAuthAuthorizationState:
    if not state:
        raise OAuthFlowError("state_missing", "The OAuth response did not include state")
    record = db.scalar(select(OAuthAuthorizationState).where(
        OAuthAuthorizationState.state_hash == _state_hash(state),
        OAuthAuthorizationState.used_at.is_(None),
    ))
    if not record:
        raise OAuthFlowError("state_invalid", "The OAuth response is invalid or was already used")
    if (_aware(record.expires_at) or utcnow()) <= utcnow():
        record.used_at = utcnow()
        db.commit()
        raise OAuthFlowError("state_expired", "The OAuth request expired; start the connection again")
    record.used_at = utcnow()
    db.commit()
    return record


def _channel_identity(credentials: Credentials) -> tuple[dict[str, Any], dict[str, Any]]:
    session = AuthorizedSession(credentials)
    response = session.get(
        YOUTUBE_CHANNELS_URL,
        params={"part": "snippet", "mine": "true", "maxResults": 1},
        timeout=30,
    )
    if not response.ok:
        raise OAuthFlowError("youtube_verification_failed", "YouTube could not verify the selected channel")
    items = response.json().get("items") or []
    if not items:
        raise OAuthFlowError("youtube_channel_missing", "The selected Google identity has no YouTube channel")
    user_response = session.get(GOOGLE_USERINFO_URL, timeout=30)
    user_info = user_response.json() if user_response.ok else {}
    return items[0], user_info


def _validated_granted_scopes(credentials: Credentials) -> list[str]:
    granted = set(credentials.granted_scopes or credentials.scopes or [])
    missing = REQUIRED_YOUTUBE_SCOPES - granted
    if missing:
        raise OAuthFlowError(
            "required_scope_missing",
            "Google authorization did not grant all required YouTube permissions",
        )
    return sorted(granted)


def complete_google_oauth(
    db: Session,
    state: str,
    code: str | None,
    provider_error: str | None = None,
) -> dict[str, Any]:
    state_record = _consume_state(db, state)
    channel = db.get(DestinationChannel, state_record.destination_channel_id)
    if not channel:
        raise OAuthFlowError("destination_missing", "The destination channel no longer exists")
    if provider_error:
        previous = channel.oauth_status
        connected = channel.google_connection and channel.google_connection.status == "connected"
        channel.oauth_status = "connected" if connected else "not_connected"
        db.add(AutomationEvent(
            aggregate_type="destination_channel", aggregate_id=channel.id,
            event_type="destination_channel.oauth_denied", from_state=previous, to_state=channel.oauth_status,
            details={"provider_error": provider_error[:80]},
        ))
        db.commit()
        raise OAuthFlowError("authorization_denied", "Google authorization was cancelled or denied")
    if not code:
        raise OAuthFlowError("code_missing", "The OAuth response did not include an authorization code")

    config = _client_config()
    code_verifier = _decrypt(state_record.code_verifier_ciphertext, config)
    if not code_verifier:
        raise OAuthFlowError("pkce_verifier_missing", "The OAuth request cannot be resumed; start the connection again")
    flow = Flow.from_client_config(
        config,
        scopes=YOUTUBE_SCOPES,
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = _redirect_uri(config["web"])
    try:
        flow.fetch_token(code=code)
        granted_scopes = _validated_granted_scopes(flow.credentials)
        youtube_channel, user_info = _channel_identity(flow.credentials)
    except OAuthFlowError as error:
        channel.oauth_status = "connected" if channel.google_connection and channel.google_connection.status == "connected" else "not_connected"
        db.add(AutomationEvent(
            aggregate_type="destination_channel", aggregate_id=channel.id,
            event_type="destination_channel.oauth_failed", from_state="authorization_pending", to_state=channel.oauth_status,
            details={"error_code": error.code},
        ))
        db.commit()
        raise
    except Exception as error:
        channel.oauth_status = "connected" if channel.google_connection and channel.google_connection.status == "connected" else "not_connected"
        db.add(AutomationEvent(
            aggregate_type="destination_channel", aggregate_id=channel.id,
            event_type="destination_channel.oauth_failed", from_state="authorization_pending", to_state=channel.oauth_status,
            details={"error_code": "token_exchange_failed", "exception_type": type(error).__name__},
        ))
        db.commit()
        raise OAuthFlowError("token_exchange_failed", "Google authorization could not be completed") from error

    authorized_channel_id = str(youtube_channel.get("id") or "")
    snippet = youtube_channel.get("snippet") or {}
    existing = db.scalar(select(GoogleConnection).where(GoogleConnection.destination_channel_id == channel.id))
    credentials = flow.credentials
    thumbnails = snippet.get("thumbnails") or {}
    thumbnail = (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url")
    if authorized_channel_id != channel.youtube_channel_id:
        previous = channel.oauth_status
        refresh_token = credentials.refresh_token
        if not refresh_token:
            _revoke_ciphertext_best_effort(
                None,
                _encrypt(credentials.token, config) if credentials.token else None,
                config,
            )
            channel.oauth_status = "connected" if existing and existing.status == "connected" else "not_connected"
            db.commit()
            raise OAuthFlowError("refresh_token_missing", "Google did not return offline access; reconnect and grant consent")
        if channel.pending_oauth_candidate:
            _remove_candidate(
                db,
                channel,
                channel.pending_oauth_candidate,
                config,
                "destination_channel.oauth_candidate_replaced",
            )
            db.flush()
        candidate = OAuthChannelCandidate(
            destination_channel_id=channel.id,
            google_subject=user_info.get("sub"),
            google_email=user_info.get("email"),
            authorized_channel_id=authorized_channel_id,
            channel_title=str(snippet.get("title") or authorized_channel_id),
            channel_thumbnail_url=thumbnail,
            access_token_ciphertext=_encrypt(credentials.token, config) if credentials.token else None,
            refresh_token_ciphertext=_encrypt(refresh_token, config),
            token_expiry=_aware(credentials.expiry),
            scopes=granted_scopes,
            expires_at=utcnow() + timedelta(minutes=CANDIDATE_TTL_MINUTES),
        )
        db.add(candidate)
        db.flush()
        channel.oauth_status = "connected" if existing and existing.status == "connected" else "channel_mismatch"
        db.add(AutomationEvent(
            aggregate_type="destination_channel", aggregate_id=channel.id,
            event_type="destination_channel.oauth_channel_mismatch",
            from_state=previous, to_state=channel.oauth_status,
            details={
                "expected_channel_id": channel.youtube_channel_id,
                "authorized_channel_id": authorized_channel_id,
                "authorized_channel_title": snippet.get("title"),
                "candidate_id": candidate.id,
            },
        ))
        db.commit()
        return {
            "status": "pending_adoption",
            "channel_id": channel.id,
            "candidate_id": candidate.id,
            "display_name": candidate.channel_title,
        }

    refresh_token = credentials.refresh_token or (_decrypt(existing.refresh_token_ciphertext, config) if existing else None)
    if not refresh_token:
        channel.oauth_status = "connected" if existing and existing.status == "connected" else "not_connected"
        db.commit()
        raise OAuthFlowError("refresh_token_missing", "Google did not return offline access; reconnect and grant consent")
    connection = existing or GoogleConnection(
        destination_channel_id=channel.id,
        authorized_channel_id=authorized_channel_id,
        channel_title=str(snippet.get("title") or channel.display_name),
        refresh_token_ciphertext=None,
    )
    connection.google_subject = user_info.get("sub")
    connection.google_email = user_info.get("email")
    connection.authorized_channel_id = authorized_channel_id
    connection.channel_title = str(snippet.get("title") or channel.display_name)
    connection.channel_thumbnail_url = thumbnail
    connection.access_token_ciphertext = _encrypt(credentials.token, config) if credentials.token else None
    connection.refresh_token_ciphertext = _encrypt(refresh_token, config)
    connection.token_expiry = _aware(credentials.expiry)
    connection.scopes = granted_scopes
    connection.status = "connected"
    connection.last_verified_at = utcnow()
    connection.revoked_at = None
    if not existing:
        db.add(connection)

    previous = channel.oauth_status
    channel.oauth_status = "connected"
    channel.operational_state = "active"
    channel.google_identity = connection.google_email
    channel.avatar_url = thumbnail
    db.execute(update(PublishJob).where(
        PublishJob.destination_channel_id == channel.id,
        PublishJob.state == "blocked_not_connected",
    ).values(state="waiting_for_upload"))
    db.add(AutomationEvent(
        aggregate_type="destination_channel", aggregate_id=channel.id,
        event_type="destination_channel.oauth_connected", from_state=previous, to_state="connected",
        details={"youtube_channel_id": authorized_channel_id, "channel_title": connection.channel_title},
    ))
    db.commit()
    return {"channel_id": channel.id, "display_name": connection.channel_title}


def adopt_oauth_candidate(db: Session, channel_id: str, candidate_id: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Destination channel not found")
    candidate = db.scalar(select(OAuthChannelCandidate).where(
        OAuthChannelCandidate.id == candidate_id,
        OAuthChannelCandidate.destination_channel_id == channel.id,
    ))
    if not candidate:
        raise HTTPException(404, "The authorized YouTube channel confirmation is no longer available")
    if (_aware(candidate.expires_at) or utcnow()) <= utcnow():
        config = _client_config()
        _remove_candidate(
            db,
            channel,
            candidate,
            config,
            "destination_channel.oauth_candidate_expired",
        )
        channel.oauth_status = "connected" if channel.google_connection and channel.google_connection.status == "connected" else "not_connected"
        db.commit()
        raise HTTPException(409, "The authorized YouTube channel confirmation expired; connect again")
    duplicate = db.scalar(select(DestinationChannel).where(
        DestinationChannel.youtube_channel_id == candidate.authorized_channel_id,
        DestinationChannel.id != channel.id,
    ))
    if duplicate:
        raise HTTPException(
            409,
            f"{candidate.channel_title} is already attached to destination {duplicate.display_name}",
        )

    previous_status = channel.oauth_status
    previous_channel_id = channel.youtube_channel_id
    connection = channel.google_connection or GoogleConnection(
        destination_channel_id=channel.id,
        authorized_channel_id=candidate.authorized_channel_id,
        channel_title=candidate.channel_title,
        refresh_token_ciphertext=candidate.refresh_token_ciphertext,
    )
    connection.google_subject = candidate.google_subject
    connection.google_email = candidate.google_email
    connection.authorized_channel_id = candidate.authorized_channel_id
    connection.channel_title = candidate.channel_title
    connection.channel_thumbnail_url = candidate.channel_thumbnail_url
    connection.access_token_ciphertext = candidate.access_token_ciphertext
    connection.refresh_token_ciphertext = candidate.refresh_token_ciphertext
    connection.token_expiry = candidate.token_expiry
    connection.scopes = candidate.scopes
    connection.status = "connected"
    connection.last_verified_at = utcnow()
    connection.revoked_at = None
    if not channel.google_connection:
        db.add(connection)

    channel.youtube_channel_id = candidate.authorized_channel_id
    channel.google_identity = candidate.google_email
    channel.avatar_url = candidate.channel_thumbnail_url
    channel.oauth_status = "connected"
    channel.operational_state = "active"
    unblocked = db.execute(update(PublishJob).where(
        PublishJob.destination_channel_id == channel.id,
        PublishJob.state == "blocked_not_connected",
    ).values(state="waiting_for_upload")).rowcount or 0
    db.delete(candidate)
    db.add(AutomationEvent(
        aggregate_type="destination_channel",
        aggregate_id=channel.id,
        event_type="destination_channel.oauth_channel_adopted",
        from_state=previous_status,
        to_state="connected",
        details={
            "previous_youtube_channel_id": previous_channel_id,
            "youtube_channel_id": connection.authorized_channel_id,
            "channel_title": connection.channel_title,
            "publish_jobs_unblocked": unblocked,
        },
    ))
    db.commit()
    return {
        "status": "connected",
        "channel_id": channel.id,
        "youtube_channel_id": channel.youtube_channel_id,
        "display_name": connection.channel_title,
    }


def discard_oauth_candidate(db: Session, channel_id: str, candidate_id: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Destination channel not found")
    candidate = db.scalar(select(OAuthChannelCandidate).where(
        OAuthChannelCandidate.id == candidate_id,
        OAuthChannelCandidate.destination_channel_id == channel.id,
    ))
    if not candidate:
        return {"status": channel.oauth_status}
    config = _client_config()
    revocation_status = _remove_candidate(
        db,
        channel,
        candidate,
        config,
        "destination_channel.oauth_candidate_discarded",
    )
    connected = channel.google_connection and channel.google_connection.status == "connected"
    channel.oauth_status = "connected" if connected else "not_connected"
    db.commit()
    return {"status": channel.oauth_status, "revocation_status": revocation_status}


def cleanup_expired_oauth_candidates(db: Session) -> int:
    candidates = db.scalars(select(OAuthChannelCandidate).where(
        OAuthChannelCandidate.expires_at <= utcnow()
    )).all()
    if not candidates:
        return 0
    try:
        config = _client_config()
    except OAuthFlowError:
        config = None
    for candidate in candidates:
        channel = db.get(DestinationChannel, candidate.destination_channel_id)
        if not channel:
            db.delete(candidate)
            continue
        if config:
            _remove_candidate(
                db,
                channel,
                candidate,
                config,
                "destination_channel.oauth_candidate_expired",
            )
        else:
            db.delete(candidate)
            db.add(AutomationEvent(
                aggregate_type="destination_channel",
                aggregate_id=channel.id,
                event_type="destination_channel.oauth_candidate_expired",
                from_state=channel.oauth_status,
                to_state=channel.oauth_status,
                details={
                    "authorized_channel_id": candidate.authorized_channel_id,
                    "authorized_channel_title": candidate.channel_title,
                    "revocation_status": "credentials_unavailable",
                },
            ))
        connected = channel.google_connection and channel.google_connection.status == "connected"
        channel.oauth_status = "connected" if connected else "not_connected"
    db.commit()
    return len(candidates)


def _credentials(connection: GoogleConnection, config: dict[str, Any]) -> Credentials:
    web = config["web"]
    return Credentials(
        token=_decrypt(connection.access_token_ciphertext, config),
        refresh_token=_decrypt(connection.refresh_token_ciphertext, config),
        token_uri=str(web.get("token_uri") or "https://oauth2.googleapis.com/token"),
        client_id=str(web["client_id"]),
        client_secret=str(web["client_secret"]),
        scopes=list(connection.scopes or YOUTUBE_SCOPES),
        expiry=_credential_expiry(connection.token_expiry),
    )


def youtube_authorized_session(db: Session, connection: GoogleConnection) -> AuthorizedSession:
    """Create a Google session and durably retain any refreshed access token."""
    config = _client_config()
    credentials = _credentials(connection, config)
    try:
        if not credentials.valid:
            credentials.refresh(GoogleRequest())
    except Exception as error:
        connection.status = "reauthorization_required"
        connection.destination_channel.oauth_status = "reauthorization_required"
        db.flush()
        raise OAuthFlowError(
            "reauthorization_required",
            "Google authorization is no longer valid; reconnect this channel",
        ) from error
    connection.access_token_ciphertext = _encrypt(credentials.token, config) if credentials.token else None
    connection.token_expiry = _aware(credentials.expiry)
    db.flush()
    return AuthorizedSession(credentials)


def encrypt_oauth_value(value: str) -> str:
    return _encrypt(value)


def decrypt_oauth_value(value: str | None) -> str | None:
    return _decrypt(value)


def verify_google_connection(db: Session, channel_id: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Destination channel not found")
    connection = db.scalar(select(GoogleConnection).where(GoogleConnection.destination_channel_id == channel.id))
    if not connection or connection.status != "connected":
        raise HTTPException(409, "This destination is not connected to Google")
    config = _client_config()
    credentials = _credentials(connection, config)
    try:
        if not credentials.valid:
            credentials.refresh(GoogleRequest())
        youtube_channel, _ = _channel_identity(credentials)
    except Exception as error:
        connection.status = "reauthorization_required"
        channel.oauth_status = "reauthorization_required"
        db.commit()
        raise HTTPException(409, "Google authorization is no longer valid; reconnect this channel") from error
    if youtube_channel.get("id") != channel.youtube_channel_id:
        connection.status = "channel_mismatch"
        channel.oauth_status = "channel_mismatch"
        db.commit()
        raise HTTPException(409, "Google authorization no longer resolves to the registered YouTube channel")
    connection.access_token_ciphertext = _encrypt(credentials.token, config) if credentials.token else None
    connection.token_expiry = _aware(credentials.expiry)
    connection.last_verified_at = utcnow()
    channel.oauth_status = "connected"
    channel.operational_state = "active"
    db.add(AutomationEvent(
        aggregate_type="destination_channel", aggregate_id=channel.id,
        event_type="destination_channel.oauth_verified", from_state="connected", to_state="connected",
        details={"youtube_channel_id": channel.youtube_channel_id},
    ))
    db.commit()
    return connection_dict(connection) or {}


def disconnect_google(db: Session, channel_id: str) -> dict[str, Any]:
    channel = db.get(DestinationChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Destination channel not found")
    config = _client_config()
    if channel.pending_oauth_candidate:
        _remove_candidate(
            db,
            channel,
            channel.pending_oauth_candidate,
            config,
            "destination_channel.oauth_candidate_discarded",
        )
    connection = db.scalar(select(GoogleConnection).where(GoogleConnection.destination_channel_id == channel.id))
    if not connection:
        channel.oauth_status = "not_connected"
        db.commit()
        return {"status": "not_connected"}
    token = _decrypt(connection.refresh_token_ciphertext, config) or _decrypt(connection.access_token_ciphertext, config)
    if token:
        try:
            response = requests.post(GOOGLE_REVOKE_URL, data={"token": token}, timeout=30)
            if response.status_code not in {200, 400}:
                raise HTTPException(502, "Google could not revoke this authorization; try again")
        except requests.RequestException as error:
            raise HTTPException(502, "Google could not be reached to revoke this authorization") from error
    connection.access_token_ciphertext = None
    connection.refresh_token_ciphertext = None
    connection.status = "revoked"
    connection.revoked_at = utcnow()
    channel.oauth_status = "not_connected"
    channel.operational_state = "paused"
    channel.google_identity = None
    db.execute(update(PublishJob).where(
        PublishJob.destination_channel_id == channel.id,
        PublishJob.state == "waiting_for_upload",
    ).values(state="blocked_not_connected"))
    db.add(AutomationEvent(
        aggregate_type="destination_channel", aggregate_id=channel.id,
        event_type="destination_channel.oauth_revoked", from_state="connected", to_state="not_connected",
        details={"youtube_channel_id": channel.youtube_channel_id},
    ))
    db.commit()
    return {"status": "not_connected"}


def frontend_callback_url(status: str, code: str | None = None, name: str | None = None) -> str:
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3001").rstrip("/")
    query = {"oauth": status}
    if code:
        query["code"] = code
    if name:
        query["channel"] = name
    return f"{frontend_url}/#channels?{urlencode(query)}"
