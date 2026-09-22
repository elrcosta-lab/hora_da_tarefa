"""Task/fila de extração (SPECS §5 v1.1) — MVP in-memory, pronto p/ Celery/Redis.

Dedupe por SHA-256: mesma foto nunca reprocessa nem rechama OpenRouter.
Backoff 1/5/30 min e concorrência 3–5 serão configurados no Celery (fase infra).
"""
import hashlib
import uuid

# store MVP: chave "{child_id}:{sha256}" → homework_id ; homework_id → registro
_UPLOADS: dict[str, str] = {}
_HOMEWORKS: dict[str, dict] = {}


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
    }
    _UPLOADS[key] = hid
    _HOMEWORKS[hid] = rec
    return rec, False


def clear_store() -> None:
    """Apenas testes."""
    _UPLOADS.clear()
    _HOMEWORKS.clear()
