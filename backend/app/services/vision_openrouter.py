"""Extração via OpenRouter gemma-4-26b-a4b-it:free (SPECS §5 v1.1).

Substituição total da IA local: anonimiza (resize/strip EXIF/hash) e chama
POST https://openrouter.ai/api/v1/chat/completions em formato OpenAI-compatible.
"""
import base64
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageFile

from app.core.config import Settings, get_settings
from app.schemas.extraction import SUBJECTS, ExtractionResult

# A1: teto de pixels contra decompression bomb (foto real de tarefa << 25MP)
Image.MAX_IMAGE_PIXELS = 25_000_000
ImageFile.LOAD_TRUNCATED_IMAGES = False


class OpenRouterRateLimited(Exception):
    """429 do tier free — retryable com backoff 1/5/30 min (Celery)."""


class ExtractionFailed(Exception):
    """Falha irrecuperável (parse, timeout esgotado, AI desabilitada)."""


def anonymize_image(image_bytes: bytes, max_side: int = 1600, quality: int = 82) -> tuple[bytes, str]:
    """Valida, remove EXIF, redimensiona e retorna (jpeg_bytes, sha256_original).

    Nunca envia o original com EXIF/GPS para a API externa (LGPD art. 14).
    """
    sha = hashlib.sha256(image_bytes).hexdigest()
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    # sem exif= → strip total de EXIF
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), sha


def _load_system_prompt() -> str:
    here = Path(__file__).resolve()
    # backend/app/services/vision_openrouter.py → raiz/ai/prompts/gemma_system.txt
    candidates = [
        here.parents[3] / "ai" / "prompts" / "gemma_system.txt",
        Path.cwd() / "ai" / "prompts" / "gemma_system.txt",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "Extraia a tarefa escolar em JSON válido."


def _get_client(settings: Settings):
    from openai import OpenAI

    return OpenAI(base_url=settings.OPENROUTER_BASE_URL, api_key=settings.OPENROUTER_API_KEY)


def _parse_json_content(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        # remove cercas markdown caso o modelo desobedeça "só JSON"
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def extract_homework(
    image_bytes: bytes,
    hint_text: str | None = None,
    client=None,
    settings: Settings | None = None,
    system_prompt: str | None = None,
) -> ExtractionResult:
    """Foto → JSON validado via OpenRouter (multimodal imagem+texto)."""
    settings = settings or get_settings()
    if not settings.AI_ENABLED:
        raise ExtractionFailed("AI_DISABLED")

    try:
        anonymized, sha = anonymize_image(
            image_bytes, max_side=settings.AI_MAX_IMAGE_SIDE, quality=settings.AI_JPEG_QUALITY
        )
    except Exception as exc:
        # inclui DecompressionBombError: falha graciosa, nunca derruba o worker
        raise ExtractionFailed(f"INVALID_IMAGE: {type(exc).__name__}") from exc
    b64 = base64.b64encode(anonymized).decode("ascii")
    system = system_prompt or _load_system_prompt()
    if hint_text:
        system = system + f"\nDica do responsável: {hint_text}"

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": system},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }
    ]

    own_client = False
    if client is None:
        client = _get_client(settings)
        own_client = True
    try:
        resp = client.chat.completions.create(
            model=settings.OPENROUTER_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=settings.OPENROUTER_TEMPERATURE,
            max_tokens=settings.OPENROUTER_MAX_TOKENS,
            timeout=settings.OPENROUTER_TIMEOUT_SECONDS,
            extra_headers={
                "HTTP-Referer": settings.OPENROUTER_SITE_URL,
                "X-Title": settings.OPENROUTER_APP_NAME,
            },
        )
    except Exception as exc:  # noqa: BLE001 — mapeia 429 para retryable
        status = getattr(exc, "status_code", None)
        if status == 429 or "429" in str(exc):
            raise OpenRouterRateLimited(str(exc)) from exc
        raise ExtractionFailed(str(exc)) from exc
    finally:
        if own_client:
            try:
                client.close()
            except Exception:
                pass

    try:
        content = resp.choices[0].message.content
        data = _parse_json_content(content)
    except Exception as exc:
        raise ExtractionFailed(f"INVALID_JSON: {exc}") from exc

    usage = getattr(resp, "usage", None)
    meta = {
        "engine": settings.OPENROUTER_MODEL,
        "provider": "openrouter",
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "image_sha256": sha,
    }

    is_hw = bool(data.get("is_homework", True))
    if not is_hw:
        return ExtractionResult(
            is_homework=False,
            confidence=float(data.get("confidence", 0.0)),
            needs_review=True,
            extraction_status="descartada",
            meta=meta,
        )

    subject = data.get("subject")
    if subject is not None and subject not in SUBJECTS:
        subject = "Outro"
    confidence = float(data.get("confidence", 0.0))
    due_at = data.get("due_at")

    needs_review = bool(data.get("needs_review", False)) or confidence < settings.AI_CONFIDENCE_OK or subject is None or due_at is None
    status = "ok" if confidence >= settings.AI_CONFIDENCE_OK and not needs_review else "baixa_confianca"
    # confiança < limiar de review → força revisão mesmo se modelo disse False
    if confidence < settings.AI_CONFIDENCE_OK:
        needs_review = True

    try:
        return ExtractionResult(
            is_homework=True,
            subject=subject,
            title=data.get("title"),
            statement=data.get("statement"),
            due_at=due_at,
            estimated_minutes=data.get("estimated_minutes"),
            priority=int(data.get("priority", 1)),
            confidence=confidence,
            needs_review=needs_review,
            extraction_status=status,
            meta=meta,
        )
    except Exception as exc:
        raise ExtractionFailed(f"VALIDATION_ERROR: {exc}") from exc
