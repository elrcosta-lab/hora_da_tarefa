"""Lembretes 24h/2h + atraso com notification_log (SPECS §2.11 §6.4, PRD RF-10).

MVP in-memory. Idempotência por key `{kind}:{homework_id}:{child_id}` (UNIQUE).
Quiet 21:30–07:00 (America/Sao_Paulo) empurra p/ 07:00. Envio real via Telegram
(aiogram + vínculo chat) entra na fase infra; aqui dispatch marca sent.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import uuid

TZ = ZoneInfo("America/Sao_Paulo")
TERMINAL_HW = {"concluida", "nao_realizada", "cancelada", "arquivada"}

_NOTIFICATIONS: dict[str, dict] = {}
_SETTINGS: dict[str, dict] = {}


def _defaults() -> dict:
    return {"lembrete_24h": True, "lembrete_2h": True, "quiet_start": "21:30", "quiet_end": "07:00"}


def get_settings(child_id: str) -> dict:
    return {**_defaults(), **_SETTINGS.get(child_id, {})}


def update_settings(child_id: str, patch: dict) -> dict:
    cur = get_settings(child_id)
    for k in ("lembrete_24h", "lembrete_2h"):
        if k in patch:
            cur[k] = bool(patch[k])
    for k in ("quiet_start", "quiet_end"):
        if k in patch and patch[k]:
            cur[k] = str(patch[k])
    _SETTINGS[child_id] = cur
    return cur


def _parse_due(due) -> datetime | None:
    if due is None:
        return None
    s = str(due)
    try:
        if len(s) == 10:
            return datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]), 23, 59, tzinfo=TZ)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt
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


def _upsert(kind: str, hw_id: str, child_id: str, scheduled_for: datetime) -> tuple[dict, bool]:
    key = _key(kind, hw_id, child_id)
    if key in _NOTIFICATIONS:
        return _NOTIFICATIONS[key], True
    rec = {
        "id": str(uuid.uuid4()),
        "homework_id": hw_id,
        "child_id": child_id,
        "kind": kind,
        "scheduled_for": scheduled_for,
        "sent_at": None,
        "status": "scheduled",
        "idempotency_key": key,
        "attempts": 0,
        "error": None,
    }
    _NOTIFICATIONS[key] = rec
    return rec, False


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

    r, _ = _upsert("sugestao_inicial", homework_id, child_id, now)
    out.append(r)

    due = _parse_due(rec.get("due_at"))
    if due is not None:
        if settings.get("lembrete_24h", True):
            sf = apply_quiet(due - timedelta(hours=24), settings["quiet_start"], settings["quiet_end"])
            r, _ = _upsert("lembrete_24h", homework_id, child_id, sf)
            out.append(r)
        if settings.get("lembrete_2h", True):
            sf = apply_quiet(due - timedelta(hours=2), settings["quiet_start"], settings["quiet_end"])
            r, _ = _upsert("lembrete_2h", homework_id, child_id, sf)
            out.append(r)
        if due < now and rec.get("status") not in TERMINAL_HW:
            r, _ = _upsert("atraso", homework_id, child_id, now)
            out.append(r)
    return out


def dispatch_due(now: datetime | None = None) -> list[dict]:
    """Beat: envia tudo scheduled com scheduled_for <= now (uma única vez cada)."""
    now = now or datetime.now(TZ)
    sent: list[dict] = []
    for rec in _NOTIFICATIONS.values():
        if rec["status"] != "scheduled":
            continue
        if rec["scheduled_for"] <= now:
            rec["status"] = "sent"
            rec["sent_at"] = now
            rec["attempts"] += 1
            sent.append(rec)
    return sent


def list_notifications(homework_id: str | None = None, child_id: str | None = None) -> list[dict]:
    items = list(_NOTIFICATIONS.values())
    if homework_id:
        items = [r for r in items if r["homework_id"] == homework_id]
    if child_id:
        items = [r for r in items if r["child_id"] == child_id]
    return sorted(items, key=lambda r: (r["scheduled_for"], r["kind"]))


def clear_notifications() -> None:
    _NOTIFICATIONS.clear()
    _SETTINGS.clear()
