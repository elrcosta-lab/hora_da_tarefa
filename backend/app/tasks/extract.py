"""Task/fila de extração (SPECS §5 v1.1) — MVP in-memory, pronto p/ Celery/Redis.

Dedupe por SHA-256: mesma foto nunca reprocessa nem rechama OpenRouter.
Backoff 1/5/30 min e concorrência 3–5 serão configurados no Celery (fase infra).
"""
import hashlib
import uuid
from datetime import datetime, timezone

# store MVP: chave "{child_id}:{sha256}" → homework_id ; homework_id → registro
_UPLOADS: dict[str, str] = {}
_HOMEWORKS: dict[str, dict] = {}
_IMAGES: dict[str, bytes] = {}


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


def get_or_create_homework(image_bytes: bytes, child_id: str, hint_text: str | None = None) -> tuple[dict, bool]:
    """Retorna (registro, deduplicated). Registro mínimo p/ 202 imediato; extração roda em background."""
    sha = sha256_bytes(image_bytes)
    key = f"{child_id}:{sha}"
    if key in _UPLOADS:
        hid = _UPLOADS[key]
        return _HOMEWORKS[hid], True
    hid = str(uuid.uuid4())
    rec = {
        "homework_id": hid,
        "child_id": child_id,
        "status": "pendente",
        "extraction_status": "processando",
        "sha256": sha,
        "hint_text": hint_text,
        "subject": None,
        "title": None,
        "statement": None,
        "due_at": None,
        "estimated_minutes": None,
        "priority": 1,
        "confidence": None,
        "needs_review": None,
    }
    _UPLOADS[key] = hid
    _HOMEWORKS[hid] = rec
    _IMAGES[hid] = image_bytes
    return rec, False


def get_homework(homework_id: str) -> dict | None:
    return _HOMEWORKS.get(homework_id)


def list_homeworks(child_id: str | None = None) -> list[dict]:
    items = list(_HOMEWORKS.values())
    if child_id:
        items = [r for r in items if r["child_id"] == child_id]
    return items


def run_extraction(homework_id: str, client=None) -> dict | None:
    """Background: anonimiza→OpenRouter→atualiza registro. Idempotente por homework_id."""
    from app.services.vision_openrouter import ExtractionFailed, OpenRouterRateLimited, extract_homework

    rec = _HOMEWORKS.get(homework_id)
    if rec is None:
        return None
    # dedupe: se já extraído com sucesso, não rechama API
    if rec.get("extraction_status") in ("ok", "baixa_confianca", "descartada"):
        return rec
    image_bytes = _IMAGES.get(homework_id)
    if image_bytes is None:
        return rec
    try:
        result = extract_homework(image_bytes, hint_text=rec.get("hint_text"), client=client)
    except OpenRouterRateLimited as exc:
        rec["extraction_status"] = "processando"
        rec["last_error"] = f"RATE_LIMITED: {exc}"
        return rec
    except ExtractionFailed as exc:
        rec["extraction_status"] = "falhou"
        rec["last_error"] = str(exc)
        return rec
    if not result.is_homework:
        rec["extraction_status"] = "descartada"
        rec["confidence"] = result.confidence
        rec["needs_review"] = True
        return rec
    rec.update(
        {
            "subject": result.subject,
            "title": result.title,
            "statement": result.statement,
            "due_at": result.due_at,
            "estimated_minutes": result.estimated_minutes,
            "priority": result.priority,
            "confidence": result.confidence,
            "needs_review": result.needs_review,
            "extraction_status": result.extraction_status,
            "extraction_json": result.model_dump(),
        }
    )
    try:
        from app.tasks import notify as _N

        _N.schedule_for_homework(homework_id)
    except Exception:
        pass
    return rec


def clear_store() -> None:
    """Apenas testes."""
    _UPLOADS.clear()
    _HOMEWORKS.clear()
    _IMAGES.clear()


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
    rec = _HOMEWORKS.get(homework_id)
    if rec is None:
        raise KeyError(homework_id)
    current = rec.get("status", "pendente")
    if new_status == current:
        return rec
    allowed = TRANSITIONS.get(current, [])
    if new_status not in allowed:
        raise StatusConflict(current, new_status)
    rec["status"] = new_status
    rec["updated_at"] = datetime.now(timezone.utc).isoformat()
    rec.setdefault("events", []).append({"from": current, "to": new_status, "at": rec["updated_at"]})
    return rec


def mark_overdue(now: str | None = None) -> list[str]:
    """Beat: pendente|agendada com due_at passado → atrasada. Nunca toca finalizadas."""
    from datetime import datetime as _dt

    marked: list[str] = []
    for hid, rec in _HOMEWORKS.items():
        if rec.get("status") not in ("pendente", "agendada"):
            continue
        due = rec.get("due_at")
        if not due:
            continue
        try:
            # due_at YYYY-MM-DD ou ISO; compara por prefixo de data
            due_day = str(due)[:10]
            today = (now or _dt.now().astimezone().isoformat())[:10]
            if due_day < today:
                rec["status"] = "atrasada"
                rec["updated_at"] = _dt.now(timezone.utc).isoformat()
                marked.append(hid)
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

    rec = _HOMEWORKS.get(homework_id)
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
    rec = _HOMEWORKS.get(homework_id)
    if rec is None:
        raise KeyError(homework_id)
    try:
        suggestions = get_suggestions(homework_id, limit=5)
    except KeyError:
        raise
    match = next((s for s in suggestions if s["start_at"] == start_at_iso), None)
    if match is None:
        raise StatusConflict(rec.get("status", "pendente"), f"slot:{start_at_iso}")
    rec["scheduled_start"] = match["start_at"]
    rec["scheduled_end"] = match["end_at"]
    if rec.get("status") == "pendente":
        transition_homework(homework_id, "agendada")
    try:
        from app.tasks import notify as _N

        _N.schedule_for_homework(homework_id)
    except Exception:
        pass
    return rec
