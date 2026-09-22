"""F0: tabelas núcleo (SPECS §2) — child, school_schedule, activity, homework, homework_image, suggestion_slot, notification_log.

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _jsonb():
    # sa.JSON renderiza em sqlite e postgres; em prod, trocar por JSONB via migração futura se preciso
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "child",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("grade_level", sa.Text(), nullable=True),
        sa.Column("school_name", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="America/Sao_Paulo"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "school_schedule",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("child_id", sa.String(36), nullable=False, index=True),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="aula"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_schedule_time_order"),
        sa.UniqueConstraint("child_id", "weekday", "start_time", "subject", name="uq_schedule_slot"),
    )
    op.create_table(
        "activity",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("child_id", sa.String(36), nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=True),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("recurrence", sa.Text(), nullable=False, server_default="weekly"),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("travel_before_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("travel_after_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "homework",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("child_id", sa.String(36), nullable=False, index=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pendente", index=True),
        sa.Column("extraction_status", sa.Text(), nullable=False, server_default="pendente"),
        sa.Column("extraction_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("extraction_json", _jsonb(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="web"),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "homework_image",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("homework_id", sa.String(36), nullable=False, index=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("preprocessed_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("homework_id", "sha256", name="uq_image_homework_sha"),
    )
    op.create_table(
        "suggestion_slot",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("homework_id", sa.String(36), nullable=False, index=True),
        sa.Column("child_id", sa.String(36), nullable=False, index=True),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Numeric(6, 3), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("homework_id", "rank", name="uq_suggestion_rank"),
    )
    op.create_table(
        "notification_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("child_id", sa.String(36), nullable=True, index=True),
        sa.Column("homework_id", sa.String(36), nullable=True, index=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="scheduled", index=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("payload", _jsonb(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in ("notification_log", "suggestion_slot", "homework_image",
                  "homework", "activity", "school_schedule", "child"):
        op.drop_table(table)
