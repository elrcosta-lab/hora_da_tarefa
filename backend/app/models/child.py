"""child + school_schedule + activity (SPECS §2.5 §2.6 §2.7)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class Child(Base):
    __tablename__ = "child"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    birth_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    grade_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    school_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="America/Sao_Paulo")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class SchoolSchedule(Base):
    __tablename__ = "school_schedule"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_schedule_time_order"),
        UniqueConstraint("child_id", "weekday", "start_time", "subject", name="uq_schedule_slot"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    child_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="aula")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class Activity(Base):
    __tablename__ = "activity"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    child_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    weekday: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    recurrence: Mapped[str] = mapped_column(Text, nullable=False, default="weekly")
    event_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    travel_before_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    travel_after_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
