"""add pending OAuth channel candidates

Revision ID: 20260928_0006
Revises: 20260928_0005
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "20260928_0006"
down_revision = "20260928_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_channel_candidates",
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
        sa.Column("refresh_token_ciphertext", sa.Text(), nullable=False),
        sa.Column("token_expiry", sa.DateTime(timezone=True)),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("oauth_channel_candidates")
