"""Persist logout revocations for JWT sessions."""
from alembic import op
import sqlalchemy as sa

revision = "0002_revoked_tokens"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # Initial schema creation also runs metadata.create_all, so tolerate an
    # already-present table when upgrading databases created by the app.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("revoked_tokens"):
        op.create_table(
            "revoked_tokens",
            sa.Column("token_hash", sa.String(length=64), primary_key=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("revoked_tokens"):
        op.drop_table("revoked_tokens")
