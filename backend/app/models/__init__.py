"""Modelos SQLAlchemy (SPECS §2 v1.1). Postgres 16 em prod, sqlite nos testes."""
from sqlalchemy.orm import declarative_base

Base = declarative_base()

from app.models.child import Activity, Child, SchoolSchedule  # noqa: E402,F401
from app.models.homework import Homework, HomeworkImage, SuggestionSlot  # noqa: E402,F401
from app.models.notify import NotificationLog  # noqa: E402,F401
