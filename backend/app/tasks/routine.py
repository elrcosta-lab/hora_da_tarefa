"""Rotina da criança (SPECS §2.5 §2.6 §2.7 §3.6 §3.7 §3.11, RF-08) — Postgres/SQLite.

Semana usa convenção Python: weekday 0=segunda … 6=domingo.
Retorna sempre dicts simples (nunca ORM detached).
"""
import uuid
from datetime import datetime, timezone

from app.core.db import session_scope
from app.models import Activity, Child, ParentAvailability, SchoolSchedule


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
            "timezone": c.timezone, "owner_user_id": c.owner_user_id}


def _schedule_to_dict(s: SchoolSchedule) -> dict:
    return {"id": s.id, "child_id": s.child_id, "weekday": s.weekday,
            "start_time": s.start_time, "end_time": s.end_time,
            "subject": s.subject, "kind": s.kind}


def _activity_to_dict(a: Activity) -> dict:
    return {"id": a.id, "child_id": a.child_id, "title": a.title, "weekday": a.weekday,
            "start_time": a.start_time, "end_time": a.end_time, "recurrence": a.recurrence,
            "travel_before_min": a.travel_before_min, "travel_after_min": a.travel_after_min,
            "is_blocking": a.is_blocking, "location": a.location}


def create_child(name: str, grade_level=None, school_name=None, birth_date=None,
                 timezone="America/Sao_Paulo", owner_user_id: str | None = None) -> dict:
    if not (name or "").strip():
        raise Validation("name é obrigatório")
    with session_scope() as s:
        rec = Child(id=str(uuid.uuid4()), name=name.strip(), grade_level=grade_level,
                    school_name=school_name, birth_date=birth_date,
                    timezone=timezone or "America/Sao_Paulo", owner_user_id=owner_user_id)
        s.add(rec)
        s.flush()
        return _child_to_dict(rec)


def list_children(owner_user_id: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = s.query(Child).order_by(Child.created_at)
        if owner_user_id is not None:
            q = q.filter_by(owner_user_id=owner_user_id)
        return [_child_to_dict(c) for c in q.all()]


def owns(child_id: str, owner_user_id: str) -> bool:
    """True se a criança existe e pertence ao dono (CA-05)."""
    with session_scope() as s:
        c = s.get(Child, child_id)
        return c is not None and c.owner_user_id == owner_user_id


def get_child(child_id: str) -> dict | None:
    with session_scope() as s:
        c = s.get(Child, child_id)
        return _child_to_dict(c) if c else None


def save_schedules(child_id: str, entries: list[dict], replace: bool = False) -> dict:
    from app.services.textnorm import normalize_subject

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
        normalized_notes: list[str] = []
        for p in parsed:
            if p["subject"].strip().lower() == "aula":
                canonical, changed = "Aula", False
            else:
                canonical, changed = normalize_subject(p["subject"])
                if changed:
                    normalized_notes.append(f"'{p['subject']}' → '{canonical}'")
            s.add(SchoolSchedule(id=str(uuid.uuid4()), child_id=child_id, weekday=p["weekday"],
                                 start_time=p["raw_start"], end_time=p["raw_end"],
                                 subject=canonical, kind=p["kind"]))
            created += 1
        s.flush()
        out = {"child_id": child_id, "created": created, "replaced": bool(replace)}
        if normalized_notes:
            out["normalized"] = normalized_notes
        return out


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


def delete_activity(child_id: str, activity_id: str) -> None:
    with session_scope() as s:
        row = s.get(Activity, activity_id)
        if row is None or row.child_id != child_id:
            raise NotFound(activity_id)
        s.delete(row)


def get_agenda(child_id: str) -> dict:
    if get_child(child_id) is None:
        raise NotFound(child_id)
    return {"child_id": child_id, "schedules": list_schedules(child_id),
            "activities": list_activities(child_id),
            "availability": list_availability(child_id)}


def _availability_to_dict(a: ParentAvailability) -> dict:
    return {"id": a.id, "child_id": a.child_id, "weekday": a.weekday,
            "start_time": a.start_time, "end_time": a.end_time,
            "kind": a.kind or "available", "week_parity": a.week_parity,
            "date": a.date.isoformat() if a.date else None}


def save_availability(child_id: str, entries: list[dict], replace: bool = False) -> dict:
    """Salva janelas do responsável (import por inferência ou manual)."""
    import re as _re

    with session_scope() as s:
        if s.get(Child, child_id) is None:
            raise NotFound(child_id)
        if replace:
            s.query(ParentAvailability).filter_by(child_id=child_id).delete()
            s.flush()
        created = 0
        for e in entries:
            wd = _check_weekday(e.get("weekday"))
            st = _parse_hm(e.get("start_time", ""))
            en = _parse_hm(e.get("end_time", ""))
            if en <= st:
                raise Validation("end_time deve ser maior que start_time")
            kind = (e.get("kind") or "available").strip().lower()
            if kind not in ("available", "busy"):
                raise Validation("kind deve ser available|busy")
            wp = e.get("week_parity")
            if wp is not None and wp not in (0, 1):
                raise Validation("week_parity deve ser 0, 1 ou null")
            dt = e.get("date")
            parsed_date = None
            if dt is not None:
                if not (isinstance(dt, str) and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", dt.strip())):
                    raise Validation("date deve ser YYYY-MM-DD ou null")
                from datetime import date as _date

                y, m, d = map(int, dt.strip().split("-"))
                parsed_date = _date(y, m, d)
            s.add(ParentAvailability(id=str(uuid.uuid4()), child_id=child_id, weekday=wd,
                                     start_time=e.get("start_time"), end_time=e.get("end_time"),
                                     kind=kind, week_parity=wp, date=parsed_date))
            created += 1
        return {"child_id": child_id, "created": created, "replaced": bool(replace)}


def list_availability(child_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.query(ParentAvailability).filter_by(child_id=child_id).order_by(
            ParentAvailability.weekday, ParentAvailability.start_time).all()
        return [_availability_to_dict(x) for x in rows]


def _schedule_key(e: dict) -> tuple:
    return (e.get("weekday"), e.get("start_time"), e.get("end_time"),
            (e.get("subject") or "").strip().lower())


def sanitize_imported_schedules(entries: list[dict]) -> tuple[list[dict], list[str]]:
    """Limpa a saída do modelo antes de persistir: dedupe exato + fusão de
    sobreposições do MESMO dia/matéria (ex.: 13:00–13:50 + 13:30–14:00).
    Matérias diferentes em choque: mantém a primeira e avisa (revisão humana).
    Retorna (entries, notes)."""
    notes: list[str] = []
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for e in entries:
        k = _schedule_key(e)
        if k in seen:
            notes.append(f"duplicada removida: dia {e.get('weekday')} {e.get('start_time')}–{e.get('end_time')}")
            continue
        seen.add(k)
        deduped.append(e)

    def _mins(hm: str) -> int:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)

    merged: list[dict] = []
    for e in sorted(deduped, key=lambda x: (x.get("weekday", 0), x.get("start_time", ""))):
        if merged and merged[-1].get("weekday") == e.get("weekday"):
            last = merged[-1]
            try:
                overlap = _mins(e["start_time"]) < _mins(last["end_time"])
            except (KeyError, ValueError):
                overlap = False
            if overlap:
                if (last.get("subject") or "").strip().lower() == (e.get("subject") or "").strip().lower():
                    if _mins(e["end_time"]) > _mins(last["end_time"]):
                        last["end_time"] = e["end_time"]
                    notes.append(f"blocos fundidos: {e.get('subject')} dia {e.get('weekday')}")
                    continue
                notes.append(f"choque mantido p/ revisão: {last.get('subject')} × {e.get('subject')} "
                             f"dia {e.get('weekday')} {e.get('start_time')}")
                # desloca o início para o fim do anterior em vez de falhar tudo
                e = dict(e, start_time=last["end_time"])
                if _mins(e["start_time"]) >= _mins(e["end_time"]):
                    notes.append(f"bloco descartado (sem duração): {e.get('subject')} dia {e.get('weekday')}")
                    continue
        merged.append(e)
    return merged, notes


def clear_routine() -> None:
    """Apenas testes."""
    with session_scope() as s:
        s.query(SchoolSchedule).delete()
        s.query(Activity).delete()
        s.query(ParentAvailability).delete()
        s.query(Child).delete()


def _now():
    return datetime.now(timezone.utc)
