"""homework + homework_image + suggestion_slot (SPECS §2.8 §2.9 §2.10)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Homework(Base):
    __tablename__ = "homework"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    child_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pendente", index=True)
    extraction_status: Mapped[str] = mapped_column(Text, nullable=False, default="pendente")
    extraction_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    extraction_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="web")
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HomeworkImage(Base):
    __tablename__ = "homework_image"
    __table_args__ = (UniqueConstraint("homework_id", "sha256", name="uq_image_homework_sha"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    homework_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    preprocessed_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SuggestionSlot(Base):
    __tablename__ = "suggestion_slot"
    __table_args__ = (UniqueConstraint("homework_id", "rank", name="uq_suggestion_rank"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    homework_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    child_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
