"""F0: parent_availability — janelas do responsável (import por inferência).

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parent_availability",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("child_id", sa.String(36), nullable=False, index=True),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_availability_time_order"),
    )


def downgrade() -> None:
    op.drop_table("parent_availability")
