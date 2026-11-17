"""persist encrypted OAuth PKCE verifier

Revision ID: 20260928_0005
Revises: 20260928_0004
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "20260928_0005"
down_revision = "20260928_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("oauth_authorization_states") as batch_op:
        batch_op.add_column(sa.Column("code_verifier_ciphertext", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("oauth_authorization_states") as batch_op:
        batch_op.drop_column("code_verifier_ciphertext")
