"""Lembretes 24h/2h + atraso com notification_log (SPECS §2.11 §6.4, PRD RF-10).

Idempotência por key `{kind}:{homework_id}:{child_id}` (UNIQUE no banco).
Quiet 21:30–07:00 (America/Sao_Paulo) empurra p/ 07:00. Envio real via Telegram
(aiogram + vínculo chat) entra na fase infra; aqui dispatch marca sent.

Retorna sempre dicts simples (nunca ORM detached).
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.db import TZ, as_aware, session_scope
from app.models import NotificationLog, NotificationSetting

TERMINAL_HW = {"concluida", "nao_realizada", "cancelada", "arquivada"}


def _defaults() -> dict:
    return {"lembrete_24h": True, "lembrete_2h": True, "quiet_start": "21:30", "quiet_end": "07:00"}


def get_settings(child_id: str) -> dict:
    with session_scope() as s:
        row = s.get(NotificationSetting, child_id)
        if row is None:
            return _defaults()
        return {"lembrete_24h": row.lembrete_24h, "lembrete_2h": row.lembrete_2h,
                "quiet_start": row.quiet_start, "quiet_end": row.quiet_end}


def update_settings(child_id: str, patch: dict) -> dict:
    cur = {**_defaults(), **{k: v for k, v in get_settings(child_id).items()}}
    for k in ("lembrete_24h", "lembrete_2h"):
        if k in patch:
            cur[k] = bool(patch[k])
    for k in ("quiet_start", "quiet_end"):
        if k in patch and patch[k]:
            cur[k] = str(patch[k])
    with session_scope() as s:
        row = s.get(NotificationSetting, child_id)
        if row is None:
            row = NotificationSetting(child_id=child_id)
            s.add(row)
        row.lembrete_24h = cur["lembrete_24h"]
        row.lembrete_2h = cur["lembrete_2h"]
        row.quiet_start = cur["quiet_start"]
        row.quiet_end = cur["quiet_end"]
        s.flush()
    return cur


def _parse_due(due) -> datetime | None:
    if due is None:
        return None
    if isinstance(due, datetime):
        return as_aware(due, TZ)
    s = str(due)
    try:
        if len(s) == 10:
            return datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]), 23, 59, tzinfo=TZ)
        dt = datetime.fromisoformat(s)
        return as_aware(dt, TZ)
    except Exception:
        return None


def _in_quiet(dt: datetime, quiet_start="21:30", quiet_end="07:00") -> bool:
    def _m(hm: str) -> int:
        h, m = map(int, hm.split(":"))
        return h * 60 + m

    cur = dt.hour * 60 + dt.minute
    qs, qe = _m(quiet_start), _m(quiet_end)
    if qs <= qe:
        return qs <= cur < qe
    return cur >= qs or cur < qe


def apply_quiet(dt: datetime, quiet_start="21:30", quiet_end="07:00") -> datetime:
    """Fora da janela de silêncio retorna igual; dentro, empurra p/ 07:00 (dia seguinte se preciso)."""
    if not _in_quiet(dt, quiet_start, quiet_end):
        return dt
    qe_h, qe_m = map(int, quiet_end.split(":"))
    cand = dt.replace(hour=qe_h, minute=qe_m, second=0, microsecond=0)
    if cand <= dt:
        cand += timedelta(days=1)
    return cand


def _key(kind: str, homework_id: str, child_id: str) -> str:
    return f"{kind}:{homework_id}:{child_id}"


def _to_dict(r: NotificationLog) -> dict:
    return {
        "id": r.id,
        "homework_id": r.homework_id,
        "child_id": r.child_id,
        "kind": r.kind,
        "scheduled_for": as_aware(r.scheduled_for, TZ),
        "sent_at": as_aware(r.sent_at, TZ) if r.sent_at else None,
        "status": r.status,
        "idempotency_key": r.idempotency_key,
        "attempts": r.attempts,
        "error": r.error,
    }


def _upsert(s, kind: str, hw_id: str, child_id: str, scheduled_for: datetime) -> tuple[dict, bool]:
    import uuid

    key = _key(kind, hw_id, child_id)
    existing = s.query(NotificationLog).filter_by(idempotency_key=key).one_or_none()
    if existing is not None:
        return _to_dict(existing), True
    naive = scheduled_for.replace(tzinfo=None) if scheduled_for.tzinfo else scheduled_for
    rec = NotificationLog(id=str(uuid.uuid4()), homework_id=hw_id, child_id=child_id,
                          kind=kind, scheduled_for=naive, status="scheduled",
                          idempotency_key=key, attempts=0)
    s.add(rec)
    s.flush()
    return _to_dict(rec), False


def schedule_for_homework(homework_id: str, now: datetime | None = None) -> list[dict]:
    """Cria sugestao_inicial + 24h/2h + atraso (se vencido). Idempotente por key."""
    from app.tasks.extract import get_homework

    rec = get_homework(homework_id)
    if rec is None:
        raise KeyError(homework_id)
    now = now or datetime.now(TZ)
    child_id = rec.get("child_id")
    settings = get_settings(child_id)
    out: list[dict] = []
    with session_scope() as s:
        r, _ = _upsert(s, "sugestao_inicial", homework_id, child_id, now)
        out.append(r)
        due = _parse_due(rec.get("due_at"))
        if due is not None:
            if settings.get("lembrete_24h", True):
                sf = apply_quiet(due - timedelta(hours=24), settings["quiet_start"], settings["quiet_end"])
                r, _ = _upsert(s, "lembrete_24h", homework_id, child_id, sf)
                out.append(r)
            if settings.get("lembrete_2h", True):
                sf = apply_quiet(due - timedelta(hours=2), settings["quiet_start"], settings["quiet_end"])
                r, _ = _upsert(s, "lembrete_2h", homework_id, child_id, sf)
                out.append(r)
            if due < now and rec.get("status") not in TERMINAL_HW:
                r, _ = _upsert(s, "atraso", homework_id, child_id, now)
                out.append(r)
    return out


def dispatch_due(now: datetime | None = None) -> list[dict]:
    """Beat: envia tudo scheduled com scheduled_for <= now (uma única vez cada)."""
    now = now or datetime.now(TZ)
    sent: list[dict] = []
    with session_scope() as s:
        rows = s.query(NotificationLog).filter_by(status="scheduled").all()
        for rec in rows:
            sf = as_aware(rec.scheduled_for, TZ)
            if sf and sf <= now:
                rec.status = "sent"
                rec.sent_at = datetime.now(TZ).replace(tzinfo=None)
                rec.attempts = (rec.attempts or 0) + 1
                s.flush()
                sent.append(_to_dict(rec))
    return sent


def list_notifications(homework_id: str | None = None, child_id: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = s.query(NotificationLog)
        if homework_id:
            q = q.filter_by(homework_id=homework_id)
        if child_id:
            q = q.filter_by(child_id=child_id)
        items = [_to_dict(r) for r in q.all()]
    return sorted(items, key=lambda r: (r["scheduled_for"], r["kind"]))


def clear_notifications() -> None:
    """Apenas testes."""
    with session_scope() as s:
        s.query(NotificationLog).delete()
        s.query(NotificationSetting).delete()
