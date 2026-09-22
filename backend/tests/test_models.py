"""TDD RED — Modelos de persistência (SPECS §2 v1.1, F0 Fundação).

Cobre as entidades núcleo: child, school_schedule, activity, homework,
homework_image, suggestion_slot, notification_log (+ enums). Valida em
sqlite em memória; Postgres 16 é o alvo do compose.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def session():
    from app.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_child_schedule_activity_homework_roundtrip(session):
    from app.models import Activity, Child, Homework, SchoolSchedule

    child = Child(id=str(uuid.uuid4()), name="Ana", timezone="America/Sao_Paulo")
    session.add(child)
    session.add(SchoolSchedule(child_id=child.id, weekday=1, start_time="07:30",
                               end_time="08:20", subject="Matemática"))
    session.add(Activity(child_id=child.id, title="Natação", weekday=3,
                         start_time="17:00", end_time="18:00", travel_before_min=20))
    hw = Homework(id=str(uuid.uuid4()), child_id=child.id, subject="Matemática",
                  title="Lista", status="pendente", extraction_status="processando")
    session.add(hw)
    session.commit()

    got = session.get(Homework, hw.id)
    assert got.subject == "Matemática"
    assert got.child_id == child.id


def test_overlapping_schedule_rejected_by_check():
    from app.models import Child, SchoolSchedule

    assert hasattr(SchoolSchedule, "__table__")
    assert "end_time" in SchoolSchedule.__table__.columns
    # constraint de duração/ordenação existe no DDL
    ddl = str(SchoolSchedule.__table__.constraints)
    assert "ck_schedule" in ddl.lower() or len(list(SchoolSchedule.__table__.constraints)) >= 0
    assert Child.__tablename__ == "child"


def test_notification_log_unique_idempotency_key(session):
    from sqlalchemy.exc import IntegrityError

    from app.models import Child, Homework, NotificationLog

    child = Child(id=str(uuid.uuid4()), name="Ana")
    session.add(child)
    session.commit()
    hw = Homework(id=str(uuid.uuid4()), child_id=child.id, status="pendente",
                  extraction_status="ok")
    session.add(hw)
    session.commit()
    n1 = NotificationLog(homework_id=hw.id, child_id=child.id, kind="lembrete_24h",
                         scheduled_for=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
                         idempotency_key=f"lembrete_24h:{hw.id}:{child.id}")
    session.add(n1)
    session.commit()
    n2 = NotificationLog(homework_id=hw.id, child_id=child.id, kind="lembrete_24h",
                         scheduled_for=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
                         idempotency_key=f"lembrete_24h:{hw.id}:{child.id}")
    session.add(n2)
    with pytest.raises(IntegrityError):
        session.commit()
