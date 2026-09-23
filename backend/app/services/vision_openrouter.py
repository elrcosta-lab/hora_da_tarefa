"""Extração via OpenRouter nex-n2.5-mini (SPECS §5 v1.1).

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
from app.schemas.extraction import ExtractionResult

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


# Prompt canônico embutido (a imagem Docker não leva ai/; arquivo faz override p/ iteração local).
DEFAULT_SYSTEM_PROMPT = """Você é um assistente que lê fotografias de tarefas escolares brasileiras
e extrai informação estruturada. Responda APENAS com um objeto JSON válido,
sem texto extra, sem markdown.

Regras:
- "subject" deve ser uma destas matérias: Matemática, Português, Redação,
  Ciências, Biologia, Física, Química, História, Geografia, Inglês, Espanhol,
  Artes, Educação Física, Ensino Religioso, Outro.
- "due_at": data de entrega no formato YYYY-MM-DD. Se aparecer "sexta",
  calcule a próxima sexta a partir da data de hoje (America/Sao_Paulo). Se não houver data clara, use null.
- "title": resumo de até 8 palavras.
- "statement": enunciado transcrito fielmente da imagem, sem inventar.
- "estimated_minutes": inteiro; estime pela quantidade de exercícios.
- "priority": 0=baixa, 1=normal, 2=alta (prova/trabalho = 2).
- "confidence": 0.0 a 1.0, sua certeza geral.
- "needs_review": true se qualquer campo crítico incerto.
- Se a imagem não for uma tarefa escolar, retorne {"is_homework": false, "confidence": 0.9, "needs_review": true}.
- Omita chaves com valor null para economizar tokens.

Esquema:
{
  "is_homework": true,
  "subject": "string",
  "title": "string",
  "statement": "string",
  "due_at": "YYYY-MM-DD|null",
  "estimated_minutes": integer|null,
  "priority": 0|1|2,
  "confidence": number,
  "needs_review": boolean
}"""


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
    return DEFAULT_SYSTEM_PROMPT


def _get_client(settings: Settings):
    from openai import OpenAI

    return OpenAI(base_url=settings.OPENROUTER_BASE_URL, api_key=settings.OPENROUTER_API_KEY)


def _parse_json_content(content: str | None) -> dict:
    if not content or not content.strip():
        raise ValueError("empty content (provider intermitente; retry cobre)")
    text = content.strip()
    if text.startswith("```"):
        # remove cercas markdown caso o modelo desobedeça "só JSON"
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _chat_json(client, settings, messages):
    """Chamada única OpenRouter (json_object, temp baixa). 429 → retryable.

    reasoning effort baixo por padrão: tokens de raciocínio são cobrados como
    saída e nossos JSONs são extração direta, sem cadeia longa.
    """
    kwargs: dict = {
        "model": settings.OPENROUTER_MODEL,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": settings.OPENROUTER_TEMPERATURE,
        "max_tokens": settings.OPENROUTER_MAX_TOKENS,
        "timeout": settings.OPENROUTER_TIMEOUT_SECONDS,
        "extra_headers": {
            "HTTP-Referer": settings.OPENROUTER_SITE_URL,
            "X-Title": settings.OPENROUTER_APP_NAME,
        },
    }
    effort = (settings.OPENROUTER_REASONING_EFFORT or "").strip().lower()
    if effort:
        # via extra_body: 'reasoning' não é parâmetro top-level do SDK openai
        kwargs["extra_body"] = {"reasoning": {"effort": effort}}
    try:
        return client.chat.completions.create(**kwargs)
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
- Omita chaves com valor null para economizar tokens.
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
    """HH:MM e variantes brasileiras (13h, 13h40, 13h40min, 13 hs) → HH:MM."""
    import re

    if isinstance(value, str):
        s = value.strip().lower().replace("hs", "").replace("horas", "").replace("hora", "").strip()
        m = re.fullmatch(r"([01]?\d|2[0-3])\s*h\s*([0-5]?\d)?\s*(?:min)?", s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2) or 0)
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
            return s
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
            image_bytes, max_side=settings.AI_LLM_MAX_SIDE, quality=settings.AI_JPEG_QUALITY
        )
        b64 = base64.b64encode(anonymized).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    own_client = client is None
    if own_client:
        client = _get_client(settings)
    try:
        _, data, _ = _parse_with_retry(client, settings, [{"role": "user", "content": parts}])
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


def _normalize_agenda_shape(data: dict) -> dict | None:
    """Agenda do dia (várias matérias) → primeira tarefa no nosso schema.

    Observado ao vivo: o modelo descreve a página inteira em vez de uma tarefa
    ({"data": "21/09/2026", "turma": ..., "tarefas": [{disciplina, tarefa, ...}]}).
    Mapeia a 1ª tarefa e sinaliza needs_review (humano confirma o resto).
    Retorna None se o formato não for agenda.
    """
    import re

    tarefas = data.get("tarefas")
    if not isinstance(tarefas, list) or not tarefas:
        return None
    first = next((t for t in tarefas if isinstance(t, dict)), None)
    if first is None:
        return None
    due = None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(data.get("data") or ""))
    if m:
        due = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    subject = (first.get("disciplina") or data.get("subject") or "Outro").strip() or "Outro"
    task = (first.get("tarefa") or "").strip()
    topic = (first.get("assunto") or "").strip()
    statement = " — ".join(p for p in (task, topic) if p) or None
    title = (topic or task).strip()
    title = " ".join(title.split()[:8]) or "Tarefa da agenda"
    try:
        conf = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        conf = 0.6
    return {"is_homework": True, "subject": subject, "title": title or "Tarefa da agenda",
            "statement": statement, "due_at": due, "estimated_minutes": None,
            "priority": 1, "confidence": min(max(conf, 0.0), 1.0), "needs_review": True,
            "_normalized_from": "agenda"}


def _parse_with_retry(client, settings, messages):
    """Parseia a resposta; em conteúdo vazio (quirk intermitente do provider),
    retenta 1× e soma usages. Retorna (resp, data, usages)."""
    usages: list = []
    try:
        resp = _chat_json(client, settings, messages)
        usages.append(getattr(resp, "usage", None))
        data = _parse_json_content(resp.choices[0].message.content)
        return resp, data, usages
    except Exception as exc:
        if "empty content" in str(exc):
            try:
                resp = _chat_json(client, settings, messages)
                usages.append(getattr(resp, "usage", None))
                return resp, _parse_json_content(resp.choices[0].message.content), usages
            except Exception as exc2:
                raise ExtractionFailed(f"INVALID_JSON: {exc2}") from exc2
        raise


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
            image_bytes, max_side=settings.AI_LLM_MAX_SIDE, quality=settings.AI_JPEG_QUALITY
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

    usages: list = []
    try:
        resp, data, usages = _parse_with_retry(client, settings, messages)
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

    if "subject" not in data and "is_homework" not in data:
        agenda = _normalize_agenda_shape(data)
        if agenda is not None:
            data = agenda

    usage = getattr(resp, "usage", None)
    prompt_tokens = sum(int(getattr(u, "prompt_tokens", 0) or 0) for u in usages if u is not None)
    completion_tokens = sum(int(getattr(u, "completion_tokens", 0) or 0) for u in usages if u is not None)
    meta = {
        "engine": settings.OPENROUTER_MODEL,
        "provider": "openrouter",
        "prompt_tokens": prompt_tokens or getattr(usage, "prompt_tokens", None),
        "completion_tokens": completion_tokens or getattr(usage, "completion_tokens", None),
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
    if subject is not None:
        from app.services.textnorm import normalize_subject

        subject, _ = normalize_subject(subject)
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
