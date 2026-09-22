"""F0: notification_settings por criança (RF-10).

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("child_id", sa.String(36), primary_key=True),
        sa.Column("lembrete_24h", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("lembrete_2h", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("quiet_start", sa.Text(), nullable=False, server_default="21:30"),
        sa.Column("quiet_end", sa.Text(), nullable=False, server_default="07:00"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notification_settings")
