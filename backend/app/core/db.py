"""Sessão/engines SQLAlchemy (F0). Postgres 16 em prod, sqlite em dev/testes.

Cada operação abre sua própria sessão curta (session_scope) — funciona em
endpoints sync/async e BackgroundTasks sem escopo compartilhado.
"""
import os
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

TZ = ZoneInfo("America/Sao_Paulo")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # dev local sem compose: sqlite em arquivo (ignorado pelo git via data/)
    path = Path(__file__).resolve().parents[2] / "data" / "app.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


@lru_cache(maxsize=8)
def get_engine(url: str | None = None):
    url = url or database_url()
    kwargs: dict = {}
    if url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, pool_pre_ping=True, **kwargs)


def session_factory(url: str | None = None) -> sessionmaker:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str | None = None):
    """Sessão curta com commit/rollback automáticos. Retorna dicts, nunca ORM detached."""
    factory = session_factory(url)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(url: str | None = None) -> None:
    from app.models import Base

    Base.metadata.create_all(get_engine(url))


def reset_db(url: str | None = None) -> None:
    """Apenas testes/dev: drop + create de todas as tabelas do metadata."""
    from app.models import Base

    engine = get_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def as_aware(dt: datetime | None, tz=TZ) -> datetime | None:
    """sqlite devolve naive; Postgres aware. Normaliza para aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt
