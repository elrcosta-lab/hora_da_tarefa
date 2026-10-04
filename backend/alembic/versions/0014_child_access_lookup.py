"""A5 (4ª rodada): lookup O(1) do código da criança (HMAC-SHA256 indexado).

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Linhas legadas (pré-0014) ficam com "" e usam o fallback de varredura no login.
    op.add_column("child_access",
                  sa.Column("code_lookup", sa.String(length=64), nullable=False, server_default=""))
    op.create_index("ix_child_access_code_lookup", "child_access", ["code_lookup"])


def downgrade() -> None:
    op.drop_index("ix_child_access_code_lookup", table_name="child_access")
    op.drop_column("child_access", "code_lookup")
