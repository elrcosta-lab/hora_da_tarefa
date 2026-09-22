"""Task/fila de extração (SPECS §5 v1.1) — Postgres/SQLite + StorageProvider.

Dedupe por SHA-256: mesma foto nunca reprocessa nem rechama OpenRouter
(UNIQUE homework_image.homework_id+sha256 + lookup prévio).
Bytes em StorageProvider (local/S3); HomeworkImage persiste metadados e
expires_at (retenção RNF-09/11, purge via purge_expired_images).

Retorna sempre dicts simples (nunca ORM detached).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from app.core.db import as_aware, session_scope
from app.core.storage import get_storage, retention_days
from app.models import Homework, HomeworkImage


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_mime(data: bytes) -> str | None:
    """Magic bytes (não confia na extensão nem no content-type do client)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _to_dict(hw: Homework) -> dict:
    return {
        "homework_id": hw.id,
        "child_id": hw.child_id,
        "created_by_user_id": hw.created_by_user_id,
        "status": hw.status,
        "extraction_status": hw.extraction_status,
        "sha256": (hw.extraction_json or {}).get("meta", {}).get("image_sha256"),
        "hint_text": None,
        "subject": hw.subject,
        "title": hw.title,
        "statement": hw.statement,
        "due_at": as_aware(hw.due_at).isoformat() if hw.due_at else None,
        "estimated_minutes": hw.estimated_minutes,
        "priority": hw.priority,
        "confidence": float(hw.extraction_confidence) if hw.extraction_confidence is not None else None,
        "needs_review": None,
        "extraction_json": hw.extraction_json,
        "scheduled_start": as_aware(hw.scheduled_start).isoformat() if hw.scheduled_start else None,
        "scheduled_end": as_aware(hw.scheduled_end).isoformat() if hw.scheduled_end else None,
        "updated_at": as_aware(hw.updated_at).isoformat() if hw.updated_at else None,
    }


def get_or_create_homework(image_bytes: bytes, child_id: str, hint_text: str | None = None,
                           created_by_user_id: str | None = None) -> tuple[dict, bool]:
    """Retorna (registro, deduplicated). Registro mínimo p/ 202 imediato; extração roda em background."""
    sha = sha256_bytes(image_bytes)
    mime = detect_mime(image_bytes) or "application/octet-stream"
    storage = get_storage()
    with session_scope() as s:
        dup = (
            s.query(HomeworkImage)
            .join(Homework, Homework.id == HomeworkImage.homework_id)
            .filter(HomeworkImage.sha256 == sha, Homework.child_id == child_id)
            .order_by(Homework.created_at)
            .first()
        )
        if dup is not None:
            hw = s.get(Homework, dup.homework_id)
            return _to_dict(hw), True
        hid = str(uuid.uuid4())
        hw = Homework(id=hid, child_id=child_id, created_by_user_id=created_by_user_id,
                      status="pendente",
                      extraction_status="processando",
                      extraction_json={"meta": {"image_sha256": sha, "hint_text": hint_text}})
        s.add(hw)
        s.flush()
        storage_key = f"original/{hid}.jpg"
        storage.put(storage_key, image_bytes, mime)
        s.add(HomeworkImage(id=str(uuid.uuid4()), homework_id=hid, storage_key=storage_key,
                            mime_type=mime, size_bytes=len(image_bytes), sha256=sha,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=retention_days())))
        s.flush()
        rec = _to_dict(hw)
        rec["hint_text"] = hint_text
        return rec, False


def get_homework(homework_id: str) -> dict | None:
    with session_scope() as s:
        hw = s.get(Homework, homework_id)
        return _to_dict(hw) if hw else None


def list_homeworks(child_id: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = s.query(Homework).order_by(Homework.created_at)
        if child_id:
            q = q.filter_by(child_id=child_id)
        return [_to_dict(hw) for hw in q.all()]


def _parse_result_due(due_str: str | None):
    """due_at do modelo (YYYY-MM-DD) → DateTime SP 23:59; None → None."""
    if not due_str:
        return None
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Sao_Paulo")
    try:
        return datetime(int(due_str[0:4]), int(due_str[5:7]), int(due_str[8:10]), 23, 59, tzinfo=tz)
    except Exception:
        return None


def run_extraction(homework_id: str, client=None) -> dict | None:
    """Background: storage→anonimiza→OpenRouter→atualiza registro. Idempotente por homework_id."""
    from app.services.vision_openrouter import ExtractionFailed, OpenRouterRateLimited, extract_homework

    with session_scope() as s:
        hw = s.get(Homework, homework_id)
        if hw is None:
            return None
        if hw.extraction_status in ("ok", "baixa_confianca", "descartada"):
            return _to_dict(hw)
        img = s.query(HomeworkImage).filter_by(homework_id=homework_id).first()
        if img is None:
            hw.extraction_status = "falhou"
            s.flush()
            return _to_dict(hw)
        try:
            image_bytes = get_storage().get(img.storage_key)
        except KeyError:
            hw.extraction_status = "falhou"
            meta = dict((hw.extraction_json or {}).get("meta", {}))
            meta["last_error"] = "IMAGE_MISSING no storage"
            hw.extraction_json = {**(hw.extraction_json or {}), "meta": meta}
            s.flush()
            return _to_dict(hw)
        hint = (hw.extraction_json or {}).get("meta", {}).get("hint_text")
        try:
            result = extract_homework(image_bytes, hint_text=hint, client=client)
        except OpenRouterRateLimited as exc:
            hw.extraction_status = "processando"
            meta = dict((hw.extraction_json or {}).get("meta", {}))
            meta["last_error"] = f"RATE_LIMITED: {exc}"
            hw.extraction_json = {**(hw.extraction_json or {}), "meta": meta}
            s.flush()
            return _to_dict(hw)
        except ExtractionFailed as exc:
            hw.extraction_status = "falhou"
            meta = dict((hw.extraction_json or {}).get("meta", {}))
            meta["last_error"] = str(exc)
            hw.extraction_json = {**(hw.extraction_json or {}), "meta": meta}
            s.flush()
            return _to_dict(hw)
        if not result.is_homework:
            hw.extraction_status = "descartada"
            hw.extraction_confidence = result.confidence
            s.flush()
            return _to_dict(hw)
        hw.subject = result.subject
        hw.title = result.title
        hw.statement = result.statement
        hw.due_at = _parse_result_due(result.due_at)
        hw.estimated_minutes = result.estimated_minutes
        hw.priority = result.priority
        hw.extraction_confidence = result.confidence
        hw.extraction_status = result.extraction_status
        hw.extraction_json = result.model_dump()
        s.flush()
        rec = _to_dict(hw)
    try:
        from app.tasks import notify as _N

        _N.schedule_for_homework(homework_id)
    except Exception:
        pass
    return rec


def clear_store() -> None:
    """Apenas testes."""
    from app.core.storage import get_storage as _get_storage

    with session_scope() as s:
        imgs = s.query(HomeworkImage).all()
        keys = [i.storage_key for i in imgs]
        s.query(HomeworkImage).delete()
        s.query(Homework).delete()
    storage = _get_storage()
    for k in keys:
        try:
            storage.delete(k)
        except Exception:
            pass


def purge_expired_images(now: datetime | None = None) -> int:
    """Cron LGPD/RNF-11: apaga bytes + linha de imagens com expires_at vencido. Retorna removidas."""
    now = now or datetime.now(timezone.utc)
    storage = get_storage()
    removed = 0
    with session_scope() as s:
        rows = s.query(HomeworkImage).all()
        for img in rows:
            exp = as_aware(img.expires_at)
            if exp is not None and exp <= now:
                try:
                    storage.delete(img.storage_key)
                except Exception:
                    pass
                s.delete(img)
                removed += 1
    return removed


def update_homework_fields(homework_id: str, **fields) -> dict:
    """Atualiza campos diretos (due_at aceita str YYYY-MM-DD/ISO ou datetime). Levanta KeyError."""
    allowed = {"due_at", "subject", "title", "statement", "estimated_minutes", "priority"}
    with session_scope() as s:
        hw = s.get(Homework, homework_id)
        if hw is None:
            raise KeyError(homework_id)
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"campo não editável: {k}")
            if k == "due_at" and isinstance(v, str):
                v = _parse_due(v, None)
            setattr(hw, k, v)
        s.flush()
        return _to_dict(hw)


# --- FSM de status (SPECS §4.6 v1.1, PRD RF-09) ---
TRANSITIONS: dict[str, list[str]] = {
    "pendente": ["agendada", "em_andamento", "cancelada", "atrasada"],
    "agendada": ["em_andamento", "cancelada", "atrasada"],
    "em_andamento": ["concluida", "nao_realizada"],
    "atrasada": ["concluida", "nao_realizada", "cancelada"],
    "concluida": ["arquivada"],
    "nao_realizada": ["arquivada"],
    "cancelada": ["arquivada"],
    "arquivada": [],
}

TERMINAL = {"concluida", "nao_realizada", "cancelada", "arquivada"}


class StatusConflict(Exception):
    def __init__(self, current: str, attempted: str):
        self.current = current
        self.attempted = attempted
        self.allowed = TRANSITIONS.get(current, [])
        super().__init__(f"{current} -> {attempted} inválido")


def transition_homework(homework_id: str, new_status: str) -> dict:
    """Transição validada pela FSM. Levanta StatusConflict fora da matriz, KeyError se inexistente."""
    with session_scope() as s:
        hw = s.get(Homework, homework_id)
        if hw is None:
            raise KeyError(homework_id)
        current = hw.status or "pendente"
        if new_status != current:
            if new_status not in TRANSITIONS.get(current, []):
                raise StatusConflict(current, new_status)
            hw.status = new_status
            hw.updated_at = datetime.now(timezone.utc)
            s.flush()
        return _to_dict(hw)


def mark_overdue(now: str | None = None) -> list[str]:
    """Beat: pendente|agendada com due_at passado → atrasada. Nunca toca finalizadas."""
    from datetime import datetime as _dt

    marked: list[str] = []
    with session_scope() as s:
        rows = s.query(Homework).filter(Homework.status.in_(["pendente", "agendada"])).all()
        today = (now or _dt.now().astimezone().isoformat())[:10]
        for hw in rows:
            if not hw.due_at:
                continue
            try:
                due_day = as_aware(hw.due_at).isoformat()[:10]
                if due_day < today:
                    hw.status = "atrasada"
                    hw.updated_at = _dt.now(timezone.utc)
                    marked.append(hw.id)
            except Exception:
                continue
    return marked


# --- Sugestões (SPECS §3.8 §3.9, RF-05/RF-06) ---
def _parse_due(due, tz):
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    tzinfo = ZoneInfo("America/Sao_Paulo") if tz is None else tz
    if due is None:
        return None
    if isinstance(due, datetime):
        dt = due
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tzinfo)
        return dt
    s = str(due)
    try:
        if len(s) == 10:  # YYYY-MM-DD → 23:59 local
            return _dt(int(s[0:4]), int(s[5:7]), int(s[8:10]), 23, 59, tzinfo=tzinfo)
        dt = _dt.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tzinfo)
        return dt
    except Exception:
        return None


def get_suggestions(homework_id: str, limit: int = 5, now=None, schedules=None, activities=None) -> list[dict]:
    """Calcula slots via scheduling.suggest_slots e serializa p/ API. Levanta KeyError se inexistente.

    Se schedules/activities não forem passados, usa a rotina real da criança (RF-08).
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    from app.services.scheduling import suggest_slots

    rec = get_homework(homework_id)
    if rec is None:
        raise KeyError(homework_id)
    if schedules is None or activities is None:
        try:
            from app.tasks import routine as _R

            child_id = rec.get("child_id")
            if schedules is None:
                schedules = [
                    {"weekday": s["weekday"], "start_time": s["start_time"],
                     "end_time": s["end_time"], "kind": s.get("kind", "aula")}
                    for s in _R.list_schedules(child_id)
                ]
            if activities is None:
                activities = [
                    {"weekday": a["weekday"], "start_time": a["start_time"], "end_time": a["end_time"],
                     "travel_before_min": a.get("travel_before_min", 0),
                     "travel_after_min": a.get("travel_after_min", 0),
                     "is_blocking": a.get("is_blocking", True)}
                    for a in _R.list_activities(child_id)
                    if a.get("weekday") is not None
                ]
        except Exception:
            schedules = schedules or []
            activities = activities or []
    tz = ZoneInfo("America/Sao_Paulo")
    now = now or _dt.now(tz)
    due = _parse_due(rec.get("due_at"), tz) or (now + __import__("datetime").timedelta(days=7))
    hw = {
        "due_at": due,
        "estimated_minutes": int(rec.get("estimated_minutes") or 30),
        "priority": int(rec.get("priority") or 1),
        "subject": rec.get("subject") or "Outro",
    }
    slots = suggest_slots(hw, schedules=schedules or [], activities=activities or [], now=now, limit=limit)
    return [
        {
            "rank": s["rank"],
            "start_at": s["start_at"].isoformat(),
            "end_at": s["end_at"].isoformat(),
            "score": s["score"],
            "reason": s["reason"],
        }
        for s in slots
    ]


def accept_suggestion(homework_id: str, start_at_iso: str) -> dict:
    """Agenda o slot escolhido (deve estar entre as sugestões atuais)."""
    rec = get_homework(homework_id)
    if rec is None:
        raise KeyError(homework_id)
    try:
        suggestions = get_suggestions(homework_id, limit=5)
    except KeyError:
        raise
    match = next((s for s in suggestions if s["start_at"] == start_at_iso), None)
    if match is None:
        raise StatusConflict(rec.get("status", "pendente"), f"slot:{start_at_iso}")
    with session_scope() as s:
        hw = s.get(Homework, homework_id)
        hw.scheduled_start = _parse_due(match["start_at"], None)
        hw.scheduled_end = _parse_due(match["end_at"], None)
        s.flush()
    if rec.get("status") == "pendente":
        transition_homework(homework_id, "agendada")
    try:
        from app.tasks import notify as _N

        _N.schedule_for_homework(homework_id)
    except Exception:
        pass
    return get_homework(homework_id)
