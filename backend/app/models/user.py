"""app_user — responsável com vínculo Telegram (SPECS §2.3/§6.1)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Text
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


def _utcnow():
    return datetime.now(timezone.utc)


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    telegram_link_code: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    lgpd_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lgpd_consent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
