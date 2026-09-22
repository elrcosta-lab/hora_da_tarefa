"""Rotina da criança: children + grade + atividades (SPECS §2.5 §2.6 §2.7 §3.6 §3.7 §3.11, RF-08).

MVP in-memory (mesmo processo da API). Semana usa convenção Python:
weekday 0=segunda … 6=domingo (compatível com o motor em scheduling.py).
"""
import uuid


_CHILDREN: dict[str, dict] = {}
_SCHEDULES: dict[str, list[dict]] = {}
_ACTIVITIES: dict[str, list[dict]] = {}


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


def create_child(name: str, grade_level=None, school_name=None, birth_date=None, timezone="America/Sao_Paulo") -> dict:
    if not (name or "").strip():
        raise Validation("name é obrigatório")
    cid = str(uuid.uuid4())
    rec = {"id": cid, "name": name.strip(), "grade_level": grade_level,
           "school_name": school_name, "birth_date": birth_date, "timezone": timezone}
    _CHILDREN[cid] = rec
    _SCHEDULES.setdefault(cid, [])
    _ACTIVITIES.setdefault(cid, [])
    return rec


def list_children() -> list[dict]:
    return list(_CHILDREN.values())


def get_child(child_id: str) -> dict | None:
    return _CHILDREN.get(child_id)


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def save_schedules(child_id: str, entries: list[dict], replace: bool = False) -> dict:
    if child_id not in _CHILDREN:
        raise NotFound(child_id)
    if replace:
        _SCHEDULES[child_id] = []
    current = _SCHEDULES.setdefault(child_id, [])
    parsed = []
    for e in entries:
        wd = _check_weekday(e.get("weekday"))
        s = _parse_hm(e.get("start_time", ""))
        en = _parse_hm(e.get("end_time", ""))
        if en <= s:
            raise Validation("end_time deve ser maior que start_time")
        dur = en - s
        if not 30 <= dur <= 480:
            raise Validation("Aula deve durar 30min–8h")
        subject = (e.get("subject") or "").strip() or "Aula"
        parsed.append({"weekday": wd, "start_min": s, "end_min": en, "subject": subject,
                       "kind": e.get("kind", "aula"), "raw_start": e.get("start_time"), "raw_end": e.get("end_time")})
    # valida sobreposição contra existente + dentro do lote
    pool = [(x["weekday"], _parse_hm(x["start_time"]), _parse_hm(x["end_time"])) for x in current]
    for p in parsed:
        for wd, s, en in pool:
            if wd == p["weekday"] and _overlaps(p["start_min"], p["end_min"], s, en):
                raise Overlap(f"Sobreposição em weekday={wd}")
        pool.append((p["weekday"], p["start_min"], p["end_min"]))
    created = 0
    for p in parsed:
        current.append({"id": str(uuid.uuid4()), "child_id": child_id, "weekday": p["weekday"],
                        "start_time": p["raw_start"], "end_time": p["raw_end"],
                        "subject": p["subject"], "kind": p["kind"]})
        created += 1
    return {"child_id": child_id, "created": created, "replaced": bool(replace)}


def list_schedules(child_id: str) -> list[dict]:
    return list(_SCHEDULES.get(child_id, []))


def add_activity(child_id: str, payload: dict) -> dict:
    if child_id not in _CHILDREN:
        raise NotFound(child_id)
    title = (payload.get("title") or "").strip()
    if not title:
        raise Validation("title é obrigatório")
    wd = payload.get("weekday")
    if wd is not None:
        wd = _check_weekday(wd)
    s = _parse_hm(payload.get("start_time", ""))
    en = _parse_hm(payload.get("end_time", ""))
    if en <= s:
        raise Validation("end_time deve ser maior que start_time")
    rec = payload.get("recurrence", "weekly")
    if rec not in ("weekly", "once", "biweekly"):
        raise Validation("recurrence deve ser weekly|once|biweekly")
    tb = int(payload.get("travel_before_min", 0))
    ta = int(payload.get("travel_after_min", 0))
    if tb < 0 or ta < 0:
        raise Validation("travel_* deve ser ≥ 0")
    rec_out = {"id": str(uuid.uuid4()), "child_id": child_id, "title": title, "weekday": wd,
               "start_time": payload.get("start_time"), "end_time": payload.get("end_time"),
               "recurrence": rec, "travel_before_min": tb, "travel_after_min": ta,
               "is_blocking": bool(payload.get("is_blocking", True)),
               "location": payload.get("location")}
    _ACTIVITIES.setdefault(child_id, []).append(rec_out)
    return rec_out


def list_activities(child_id: str) -> list[dict]:
    return list(_ACTIVITIES.get(child_id, []))


def get_agenda(child_id: str) -> dict:
    if child_id not in _CHILDREN:
        raise NotFound(child_id)
    return {"child_id": child_id, "schedules": list_schedules(child_id), "activities": list_activities(child_id)}


def clear_routine() -> None:
    _CHILDREN.clear()
    _SCHEDULES.clear()
    _ACTIVITIES.clear()
