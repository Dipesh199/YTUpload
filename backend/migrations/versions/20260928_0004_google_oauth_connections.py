"""add encrypted Google OAuth connections

Revision ID: 20260928_0004
Revises: 20260928_0003
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "20260928_0004"
down_revision = "20260928_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "destination_channel_id",
            sa.String(36),
            sa.ForeignKey("destination_channels.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("google_subject", sa.String(255)),
        sa.Column("google_email", sa.String(255)),
        sa.Column("authorized_channel_id", sa.String(64), nullable=False),
        sa.Column("channel_title", sa.String(255), nullable=False),
        sa.Column("channel_thumbnail_url", sa.Text()),
        sa.Column("access_token_ciphertext", sa.Text()),
        sa.Column("refresh_token_ciphertext", sa.Text()),
        sa.Column("token_expiry", sa.DateTime(timezone=True)),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "oauth_authorization_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "destination_channel_id",
            sa.String(36),
            sa.ForeignKey("destination_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_oauth_authorization_states_destination_channel_id",
        "oauth_authorization_states",
        ["destination_channel_id"],
    )


def downgrade() -> None:
    op.drop_table("oauth_authorization_states")
    op.drop_table("google_connections")
