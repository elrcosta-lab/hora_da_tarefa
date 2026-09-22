"""F0: consentimento LGPD no cadastro (RF-01).

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("lgpd_consent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("app_user", sa.Column("lgpd_consent_version", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_user", "lgpd_consent_version")
    op.drop_column("app_user", "lgpd_consent_at")
