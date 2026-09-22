"""Task/fila de extração (SPECS §5 v1.1) — MVP in-memory, pronto p/ Celery/Redis.

Dedupe por SHA-256: mesma foto nunca reprocessa nem rechama OpenRouter.
Backoff 1/5/30 min e concorrência 3–5 serão configurados no Celery (fase infra).
"""
import hashlib
import uuid

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
    return rec


def clear_store() -> None:
    """Apenas testes."""
    _UPLOADS.clear()
    _HOMEWORKS.clear()
    _IMAGES.clear()
