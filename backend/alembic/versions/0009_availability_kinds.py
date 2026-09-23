"""F0: parent_availability.kind/week_parity/date (escala de trabalho do responsável).

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parent_availability", sa.Column("kind", sa.Text(), nullable=False, server_default="available"))
    op.add_column("parent_availability", sa.Column("week_parity", sa.SmallInteger(), nullable=True))
    op.add_column("parent_availability", sa.Column("date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("parent_availability", "date")
    op.drop_column("parent_availability", "week_parity")
    op.drop_column("parent_availability", "kind")
