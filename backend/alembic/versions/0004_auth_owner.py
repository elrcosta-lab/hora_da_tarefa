"""F0: auth por senha + dono da criança.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("email", sa.Text(), nullable=True))
    op.add_column("app_user", sa.Column("password_hash", sa.Text(), nullable=True))
    op.create_index("uq_app_user_email", "app_user", ["email"], unique=True)
    op.add_column("child", sa.Column("owner_user_id", sa.Text(), nullable=True))
    op.create_index("ix_child_owner", "child", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_child_owner", table_name="child")
    op.drop_column("child", "owner_user_id")
    op.drop_index("uq_app_user_email", table_name="app_user")
    op.drop_column("app_user", "password_hash")
    op.drop_column("app_user", "email")
