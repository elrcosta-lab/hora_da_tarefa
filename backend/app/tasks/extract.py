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
    needs_review = None
    if hw.extraction_status not in ("descartada", "falhou", "processando"):
        needs_review = not (hw.subject and hw.due_at)
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
        "needs_review": needs_review,
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


def list_homeworks(child_id: str | None = None, owner_user_id: str | None = None,
                   status: list[str] | None = None, subject: str | None = None,
                   due_before: str | None = None, due_after: str | None = None,
                   q: str | None = None, sort: str = "created_at") -> list[dict]:
    """Lista com filtros combinados (RF-12). due_* aceitam YYYY-MM-DD ou ISO; q busca em title/statement."""
    from app.models import Child

    with session_scope() as s:
        query = s.query(Homework).order_by(Homework.created_at)
        if child_id:
            query = query.filter_by(child_id=child_id)
        if owner_user_id is not None:
            query = query.join(Child, Child.id == Homework.child_id).filter(
                Child.owner_user_id == owner_user_id)
        items = [_to_dict(hw) for hw in query.all()]

    if status:
        wanted = {t.strip() for t in status} if isinstance(status, (list, tuple, set)) else {status}
        items = [r for r in items if r.get("status") in wanted]
    if subject:
        items = [r for r in items if (r.get("subject") or "").lower() == subject.lower()]
    if due_before:
        items = [r for r in items if r.get("due_at") and r["due_at"][:10] < due_before[:10]]
    if due_after:
        items = [r for r in items if r.get("due_at") and r["due_at"][:10] > due_after[:10]]
    if q:
        needle = q.lower()
        items = [r for r in items
                 if needle in (r.get("title") or "").lower() or needle in (r.get("statement") or "").lower()]
    reverse = sort.startswith("-")
    key = sort.lstrip("-")
    if key == "due_at":
        items.sort(key=lambda r: (r.get("due_at") or "9999", r["homework_id"]), reverse=reverse)
    return items


def today_overview(owner_user_id: str, child_id: str | None = None, date: str | None = None) -> dict:
    """Dashboard Hoje (RF-11): tarefas vencendo hoje, atrasadas e agendadas p/ hoje. Data SP YYYY-MM-DD."""
    from datetime import datetime as _dt

    from app.core.db import TZ

    today = (date or _dt.now(TZ).isoformat())[:10]
    items = list_homeworks(child_id=child_id, owner_user_id=owner_user_id)

    def _item(r: dict) -> dict:
        return {"id": r["homework_id"], "child_id": r["child_id"], "subject": r.get("subject"),
                "title": r.get("title"), "due_at": r.get("due_at"), "status": r.get("status"),
                "extraction_confidence": r.get("confidence"),
                "estimated_minutes": r.get("estimated_minutes"),
                "scheduled_start": r.get("scheduled_start")}

    due_today = [_item(r) for r in items if r.get("due_at") and r["due_at"][:10] == today
                 and r.get("status") not in TERMINAL]
    overdue = [_item(r) for r in items if r.get("status") == "atrasada"
               or (r.get("due_at") and r["due_at"][:10] < today
                   and r.get("status") in ("pendente", "agendada"))]
    scheduled = [_item(r) for r in items if r.get("scheduled_start") and r["scheduled_start"][:10] == today
                 and r.get("status") not in TERMINAL]
    return {"date": today, "due_today": due_today, "overdue": overdue, "scheduled_today": scheduled}


def export_csv(owner_user_id: str, child_id: str | None = None, status=None, subject: str | None = None,
               due_before: str | None = None, due_after: str | None = None,
               q: str | None = None, sort: str = "created_at") -> str:
    """CSV UTF-8 (RF-12) com os mesmos filtros da listagem. Chamador adiciona BOM p/ Excel."""
    import csv as _csv
    import io as _io

    items = list_homeworks(child_id=child_id, owner_user_id=owner_user_id, status=status,
                           subject=subject, due_before=due_before, due_after=due_after,
                           q=q, sort=sort)
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["id", "child_id", "subject", "title", "due_at", "status",
                "scheduled_start", "estimated_minutes", "priority"])
    for r in items:
        w.writerow([r["homework_id"], r["child_id"], r.get("subject") or "",
                    r.get("title") or "", r.get("due_at") or "", r.get("status"),
                    r.get("scheduled_start") or "", r.get("estimated_minutes") or "",
                    r.get("priority", 1)])
    return buf.getvalue()


def homework_owner_id(homework_id: str) -> str | None:
    """Dono da tarefa via criança. None se tarefa/criança inexistente."""
    from app.models import Child

    with session_scope() as s:
        hw = s.get(Homework, homework_id)
        if hw is None:
            return None
        c = s.get(Child, hw.child_id)
        return c.owner_user_id if c else None


def owned_by(homework_id: str, owner_user_id: str) -> bool:
    return homework_owner_id(homework_id) == owner_user_id


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
    """Edição humana (RF-06). Valida; preencher críticos promove baixa_confianca → ok."""
    allowed = {"due_at", "subject", "title", "statement", "estimated_minutes", "priority"}
    with session_scope() as s:
        hw = s.get(Homework, homework_id)
        if hw is None:
            raise KeyError(homework_id)
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"campo não editável: {k}")
            if k == "due_at" and isinstance(v, str):
                parsed = _parse_due(v, None)
                if v is not None and parsed is None:
                    raise ValueError(f"due_at inválida: {v!r} (use YYYY-MM-DD ou ISO)")
                v = parsed
            if k == "priority" and v is not None and int(v) not in (0, 1, 2):
                raise ValueError("priority deve ser 0, 1 ou 2")
            if k == "estimated_minutes" and v is not None and int(v) <= 0:
                raise ValueError("estimated_minutes deve ser > 0")
            setattr(hw, k, v)
        if hw.subject and hw.due_at and hw.extraction_status == "baixa_confianca":
            hw.extraction_status = "ok"
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
