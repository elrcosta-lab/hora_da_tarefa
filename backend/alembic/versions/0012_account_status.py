"""RF-16: status de aprovação da conta (pending/approved/rejected).

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("status", sa.Text(), nullable=False, server_default="pending"))
    # backfill: contas existentes (inclui admin) já operam → approved
    op.execute("UPDATE app_user SET status='approved' WHERE status='pending'")


def downgrade() -> None:
    op.drop_column("app_user", "status")
