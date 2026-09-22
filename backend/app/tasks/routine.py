"""Rotina da criança (SPECS §2.5 §2.6 §2.7 §3.6 §3.7 §3.11, RF-08) — Postgres/SQLite.

Semana usa convenção Python: weekday 0=segunda … 6=domingo.
Retorna sempre dicts simples (nunca ORM detached).
"""
import uuid
from datetime import datetime, timezone

from app.core.db import session_scope
from app.models import Activity, Child, SchoolSchedule


class NotFound(Exception):
    pass


class Validation(Exception):
    pass


class Overlap(Exception):
    pass


def _parse_hm(s: str) -> int:
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
    except Exception:
        raise Validation(f"Hora inválida: {s!r} (use HH:MM)")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise Validation(f"Hora inválida: {s!r}")
    return h * 60 + m


def _check_weekday(wd) -> int:
    try:
        wd = int(wd)
    except Exception:
        raise Validation(f"weekday inválido: {wd!r}")
    if not 0 <= wd <= 6:
        raise Validation(f"weekday deve ser 0–6, recebido {wd}")
    return wd


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _child_to_dict(c: Child) -> dict:
    return {"id": c.id, "name": c.name, "grade_level": c.grade_level,
            "school_name": c.school_name,
            "birth_date": c.birth_date.isoformat() if c.birth_date else None,
            "timezone": c.timezone}


def _schedule_to_dict(s: SchoolSchedule) -> dict:
    return {"id": s.id, "child_id": s.child_id, "weekday": s.weekday,
            "start_time": s.start_time, "end_time": s.end_time,
            "subject": s.subject, "kind": s.kind}


def _activity_to_dict(a: Activity) -> dict:
    return {"id": a.id, "child_id": a.child_id, "title": a.title, "weekday": a.weekday,
            "start_time": a.start_time, "end_time": a.end_time, "recurrence": a.recurrence,
            "travel_before_min": a.travel_before_min, "travel_after_min": a.travel_after_min,
            "is_blocking": a.is_blocking, "location": a.location}


def create_child(name: str, grade_level=None, school_name=None, birth_date=None, timezone="America/Sao_Paulo") -> dict:
    if not (name or "").strip():
        raise Validation("name é obrigatório")
    with session_scope() as s:
        rec = Child(id=str(uuid.uuid4()), name=name.strip(), grade_level=grade_level,
                    school_name=school_name, birth_date=birth_date, timezone=timezone or "America/Sao_Paulo")
        s.add(rec)
        s.flush()
        return _child_to_dict(rec)


def list_children() -> list[dict]:
    with session_scope() as s:
        return [_child_to_dict(c) for c in s.query(Child).order_by(Child.created_at).all()]


def get_child(child_id: str) -> dict | None:
    with session_scope() as s:
        c = s.get(Child, child_id)
        return _child_to_dict(c) if c else None


def save_schedules(child_id: str, entries: list[dict], replace: bool = False) -> dict:
    with session_scope() as s:
        if s.get(Child, child_id) is None:
            raise NotFound(child_id)
        if replace:
            s.query(SchoolSchedule).filter_by(child_id=child_id).delete()
            s.flush()
        current = s.query(SchoolSchedule).filter_by(child_id=child_id).all()
        parsed = []
        for e in entries:
            wd = _check_weekday(e.get("weekday"))
            st = _parse_hm(e.get("start_time", ""))
            en = _parse_hm(e.get("end_time", ""))
            if en <= st:
                raise Validation("end_time deve ser maior que start_time")
            if not 30 <= (en - st) <= 480:
                raise Validation("Aula deve durar 30min–8h")
            parsed.append({"weekday": wd, "start_min": st, "end_min": en,
                           "subject": (e.get("subject") or "").strip() or "Aula",
                           "kind": e.get("kind", "aula"),
                           "raw_start": e.get("start_time"), "raw_end": e.get("end_time")})
        pool = [(x.weekday, _parse_hm(x.start_time), _parse_hm(x.end_time)) for x in current]
        for p in parsed:
            for wd, bst, ben in pool:
                if wd == p["weekday"] and _overlaps(p["start_min"], p["end_min"], bst, ben):
                    raise Overlap(f"Sobreposição em weekday={wd}")
            pool.append((p["weekday"], p["start_min"], p["end_min"]))
        created = 0
        for p in parsed:
            s.add(SchoolSchedule(id=str(uuid.uuid4()), child_id=child_id, weekday=p["weekday"],
                                 start_time=p["raw_start"], end_time=p["raw_end"],
                                 subject=p["subject"], kind=p["kind"]))
            created += 1
        return {"child_id": child_id, "created": created, "replaced": bool(replace)}


def list_schedules(child_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.query(SchoolSchedule).filter_by(child_id=child_id).order_by(
            SchoolSchedule.weekday, SchoolSchedule.start_time).all()
        return [_schedule_to_dict(x) for x in rows]


def add_activity(child_id: str, payload: dict) -> dict:
    with session_scope() as s:
        if s.get(Child, child_id) is None:
            raise NotFound(child_id)
        title = (payload.get("title") or "").strip()
        if not title:
            raise Validation("title é obrigatório")
        wd = payload.get("weekday")
        if wd is not None:
            wd = _check_weekday(wd)
        st = _parse_hm(payload.get("start_time", ""))
        en = _parse_hm(payload.get("end_time", ""))
        if en <= st:
            raise Validation("end_time deve ser maior que start_time")
        rec = payload.get("recurrence", "weekly")
        if rec not in ("weekly", "once", "biweekly"):
            raise Validation("recurrence deve ser weekly|once|biweekly")
        tb = int(payload.get("travel_before_min", 0))
        ta = int(payload.get("travel_after_min", 0))
        if tb < 0 or ta < 0:
            raise Validation("travel_* deve ser ≥ 0")
        row = Activity(id=str(uuid.uuid4()), child_id=child_id, title=title, weekday=wd,
                       start_time=payload.get("start_time"), end_time=payload.get("end_time"),
                       recurrence=rec, travel_before_min=tb, travel_after_min=ta,
                       is_blocking=bool(payload.get("is_blocking", True)),
                       location=payload.get("location"))
        s.add(row)
        s.flush()
        return _activity_to_dict(row)


def list_activities(child_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.query(Activity).filter_by(child_id=child_id).order_by(Activity.created_at).all()
        return [_activity_to_dict(x) for x in rows]


def get_agenda(child_id: str) -> dict:
    if get_child(child_id) is None:
        raise NotFound(child_id)
    return {"child_id": child_id, "schedules": list_schedules(child_id),
            "activities": list_activities(child_id)}


def clear_routine() -> None:
    """Apenas testes."""
    with session_scope() as s:
        s.query(SchoolSchedule).delete()
        s.query(Activity).delete()
        s.query(Child).delete()


def _now():
    return datetime.now(timezone.utc)
