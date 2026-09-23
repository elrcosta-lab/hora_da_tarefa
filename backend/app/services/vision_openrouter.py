"""Extração via OpenRouter nex-n2.5-mini:free (SPECS §5 v1.1).

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
    # backend/app/services/vision_openrouter.py → raiz/ai/prompts/nex_system.txt
    candidates = [
        here.parents[3] / "ai" / "prompts" / "nex_system.txt",
        Path.cwd() / "ai" / "prompts" / "nex_system.txt",
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


def _chat_json(client, settings, messages):
    """Chamada única OpenRouter (json_object, temp baixa). 429 → retryable."""
    try:
        return client.chat.completions.create(
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
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status_code", None)
        if status == 429 or "429" in str(exc):
            raise OpenRouterRateLimited(str(exc)) from exc
        raise ExtractionFailed(str(exc)) from exc


_WEEKDAY_PT = {
    "seg": 0, "segunda": 0, "segunda-feira": 0, "segunda feira": 0,
    "ter": 1, "terca": 1, "terça": 1, "terca-feira": 1, "terça-feira": 1,
    "qua": 2, "quarta": 2, "quarta-feira": 2, "quarta feira": 2,
    "qui": 3, "quinta": 3, "quinta-feira": 3, "quinta feira": 3,
    "sex": 4, "sexta": 4, "sexta-feira": 4, "sexta feira": 4,
    "sab": 5, "sabado": 5, "sábado": 5,
    "dom": 6, "domingo": 6,
    "mon": 0, "monday": 0, "tue": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

_ROUTINE_SYSTEM = """Você lê rotinas escolares brasileiras (texto ou foto de grade/horário/bilhete) e extrai estrutura.
Responda APENAS JSON válido, sem markdown.
- weekday é NÚMERO: 0=segunda, 1=terça, 2=quarta, 3=quinta, 4=sexta, 5=sábado, 6=domingo. Exemplos: "quarta" → 2, "seg a sex" → [0,1,2,3,4].
- horários em HH:MM (24h). Se só houver turno ("manhã"), use null e avise em warnings.
- availability: janelas em que o responsável pode acompanhar a tarefa ("posso", "livre", "disponível", "após as 18h").
- Nunca invente horários; o que for ilegível vai para warnings e a entrada é descartada.
Esquema: {"schedules": [{"weekday": int, "start_time": "HH:MM", "end_time": "HH:MM", "subject": str, "kind": "aula"}],
"activities": [{"title": str, "weekday": int|null, "start_time": "HH:MM", "end_time": "HH:MM", "recurrence": "weekly", "travel_before_min": int, "travel_after_min": int, "is_blocking": bool}],
"availability": [{"weekday": int, "start_time": "HH:MM", "end_time": "HH:MM"}],
"confidence": number, "needs_review": bool, "warnings": [str]}"""


def _expand_weekdays(value, warnings: list, where: str) -> list | None:
    """Lista de weekdays (ex.: 'seg a sex' → [0..4]) ou None p/ valor único."""
    if not isinstance(value, list):
        return None
    out = []
    for v in value:
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and 0 <= v <= 6:
            out.append(v)
        elif isinstance(v, str) and v.strip().lower() in _WEEKDAY_PT:
            out.append(_WEEKDAY_PT[v.strip().lower()])
        else:
            warnings.append(f"{where}: weekday {v!r} ignorado na lista")
    return out


def _coerce_weekday(value, warnings: list, where: str):
    if isinstance(value, bool):
        warnings.append(f"{where}: weekday inválido {value!r} (descartado)")
        return None
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _WEEKDAY_PT:
            return _WEEKDAY_PT[key]
    warnings.append(f"{where}: weekday inválido {value!r} (descartado)")
    return None


def _coerce_hm(value, warnings: list, where: str):
    import re

    if isinstance(value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value.strip()):
        return value.strip()
    warnings.append(f"{where}: horário inválido {value!r} (descartado)")
    return None


def extract_routine(text: str | None = None, image_bytes: bytes | None = None,
                    client=None, settings: Settings | None = None):
    """Texto ou imagem de rotina → RoutineExtractionResult (grade+atividades+disponibilidade)."""
    from app.schemas.routine import RoutineExtractionResult

    settings = settings or get_settings()
    if not settings.AI_ENABLED:
        raise ExtractionFailed("AI_DISABLED")
    if not (text or "").strip() and image_bytes is None:
        raise ExtractionFailed("EMPTY_INPUT: informe texto ou imagem")

    parts: list = [{"type": "text", "text": _ROUTINE_SYSTEM + "\n\nRotina:\n" + (text or "").strip()}]
    if image_bytes is not None:
        anonymized, _ = anonymize_image(
            image_bytes, max_side=settings.AI_MAX_IMAGE_SIDE, quality=settings.AI_JPEG_QUALITY
        )
        b64 = base64.b64encode(anonymized).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    own_client = client is None
    if own_client:
        client = _get_client(settings)
    try:
        resp = _chat_json(client, settings, [{"role": "user", "content": parts}])
        data = _parse_json_content(resp.choices[0].message.content)
    except (OpenRouterRateLimited, ExtractionFailed):
        raise
    except Exception as exc:
        raise ExtractionFailed(f"INVALID_JSON: {exc}") from exc
    finally:
        if own_client:
            try:
                client.close()
            except Exception:
                pass

    warnings: list = list(data.get("warnings") or [])

    def _entry(raw: dict, kinds: tuple, where: str, weekday_required: bool = True):
        if not isinstance(raw, dict):
            warnings.append(f"{where}: entrada inválida (descartada)")
            return None
        out: dict = {}
        for k in kinds:
            if k in ("weekday", "start_time", "end_time"):
                continue
            out[k] = raw.get(k)
        wd = None
        if "weekday" in kinds:
            if raw.get("weekday") is None and not weekday_required:
                wd = None
            else:
                wd = _coerce_weekday(raw.get("weekday"), warnings, where)
        if "weekday" in kinds:
            if wd is None and weekday_required:
                return None
            if wd is not None:
                out["weekday"] = wd
        for hk in ("start_time", "end_time"):
            if hk in kinds:
                hv = _coerce_hm(raw.get(hk), warnings, where)
                if hv is None:
                    return None
                out[hk] = hv
        return out

    schedules, activities, availability = [], [], []

    def _variants(raw: dict, where: str):
        """Expande weekday lista ([0..4]) em uma cópia por dia; valor único passa direto."""
        exp = _expand_weekdays(raw.get("weekday"), warnings, where)
        if exp is None:
            return [raw]
        if not exp:
            warnings.append(f"{where}: nenhum weekday válido (descartada)")
            return []
        return [{**raw, "weekday": wd} for wd in exp]

    for i, raw in enumerate(data.get("schedules") or []):
        for v in _variants(raw, f"schedules[{i}]"):
            e = _entry(v, ("weekday", "start_time", "end_time", "subject", "kind"),
                       f"schedules[{i}]")
            if e:
                e.setdefault("subject", "Aula")
                e.setdefault("kind", "aula")
                schedules.append(e)
    for i, raw in enumerate(data.get("activities") or []):
        for v in _variants(raw, f"activities[{i}]"):
            e = _entry(v, ("title", "weekday", "start_time", "end_time", "recurrence",
                           "travel_before_min", "travel_after_min", "is_blocking"),
                       f"activities[{i}]", weekday_required=False)
            if e and (e.get("title") or "").strip():
                e.setdefault("recurrence", "weekly")
                e.setdefault("travel_before_min", 0)
                e.setdefault("travel_after_min", 0)
                e.setdefault("is_blocking", True)
                activities.append(e)
            elif e:
                warnings.append(f"activities[{i}]: sem título (descartada)")
    for i, raw in enumerate(data.get("availability") or []):
        for v in _variants(raw, f"availability[{i}]"):
            e = _entry(v, ("weekday", "start_time", "end_time"), f"availability[{i}]")
            if e:
                availability.append(e)

    try:
        return RoutineExtractionResult(
            schedules=schedules, activities=activities, availability=availability,
            confidence=float(data.get("confidence", 0.0)),
            needs_review=bool(data.get("needs_review", True)),
            warnings=warnings,
        )
    except Exception as exc:
        raise ExtractionFailed(f"VALIDATION_ERROR: {exc}") from exc


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
        resp = _chat_json(client, settings, messages)
    except OpenRouterRateLimited:
        raise
    except ExtractionFailed:
        raise
    except Exception as exc:
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
