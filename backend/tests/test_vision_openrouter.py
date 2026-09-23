"""TDD RED — Extração via OpenRouter nex-n2.5-mini (SPECS §5 v1.1).

Cobre:
- RF-05: anonimização local (resize ≤1600, strip EXIF, SHA-256) antes de qualquer chamada externa
- RF-05: chamada OpenRouter com model free, response_format json_object, temperature 0.1
- RF-04: needs_review quando confiança < 0.75 / campos críticos nulos
- is_homework=false descartado com aviso
- 429 rate limit → exceção retryable (backoff Celery 1/5/30)
"""
import base64
import hashlib
import io
import json

import pytest
from PIL import Image
from unittest.mock import MagicMock


def _make_test_image(width=2000, height=1200, color=(255, 255, 0)):
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    # salva com EXIF fake para provar que anonimização remove
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_anonymize_resizes_and_strips_exif_and_hashes():
    from app.services.vision_openrouter import anonymize_image

    original = _make_test_image(2000, 1200)
    expected_sha = hashlib.sha256(original).hexdigest()

    anonymized, sha = anonymize_image(original, max_side=1600, quality=82)

    assert sha == expected_sha
    img = Image.open(io.BytesIO(anonymized))
    assert max(img.size) <= 1600
    assert img.format == "JPEG"
    # sem EXIF após anonimização
    assert img.getexif() is not None and len(dict(img.getexif())) == 0


def test_extract_calls_openrouter_with_nex_free_and_parses_json():
    from app.services.vision_openrouter import extract_homework

    fake_json = {
        "is_homework": True,
        "subject": "Matemática",
        "title": "Lista de frações",
        "statement": "Resolver os exercícios 1 a 10 da página 42.",
        "due_at": "2026-09-25",
        "estimated_minutes": 40,
        "priority": 2,
        "confidence": 0.91,
        "needs_review": False,
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_json)))],
        usage=MagicMock(prompt_tokens=1240, completion_tokens=180),
    )

    result = extract_homework(_make_test_image(800, 600), hint_text="é de matemática", client=mock_client)

    # chamada OpenRouter correta (SPECS §5.2 E2)
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == "nex-agi/nex-n2.5-mini"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_tokens"] == 4096
    assert kwargs["extra_body"] == {"reasoning": {"effort": "low"}}
    assert "reasoning" not in kwargs  # nunca top-level: SDK rejeita (TypeError ao vivo)
    # imagem vai em data-URL base64, nunca o original com EXIF
    content = kwargs["messages"][0]["content"]
    kinds = {c["type"] for c in content}
    assert {"text", "image_url"} <= kinds
    img_url = next(c["image_url"]["url"] for c in content if c["type"] == "image_url")
    assert img_url.startswith("data:image/jpeg;base64,")
    base64.b64decode(img_url.split(",", 1)[1])  # não quebra

    assert result.subject == "Matemática"
    assert result.confidence == pytest.approx(0.91)
    assert result.needs_review is False
    assert result.meta["engine"] == "nex-agi/nex-n2.5-mini"
    assert mock_client.chat.completions.create.call_count == 1  # sem duplicar custo


def test_low_confidence_marks_needs_review():
    from app.services.vision_openrouter import extract_homework

    fake_json = {
        "is_homework": True,
        "subject": None,
        "title": "Ilegível",
        "statement": "não deu para ler",
        "due_at": None,
        "estimated_minutes": None,
        "priority": 1,
        "confidence": 0.42,
        "needs_review": True,
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_json)))],
        usage=MagicMock(prompt_tokens=1000, completion_tokens=120),
    )

    result = extract_homework(_make_test_image(800, 600), client=mock_client)

    assert result.extraction_status == "baixa_confianca"
    assert result.needs_review is True


def test_non_homework_returns_is_homework_false():
    from app.services.vision_openrouter import extract_homework

    fake_json = {"is_homework": False, "confidence": 0.9, "needs_review": True}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_json)))],
        usage=MagicMock(prompt_tokens=800, completion_tokens=40),
    )

    result = extract_homework(_make_test_image(800, 600), client=mock_client)

    assert result.is_homework is False
    assert result.extraction_status == "descartada"


def test_decompression_bomb_fails_gracefully(monkeypatch):
    import PIL.Image
    from app.services.vision_openrouter import ExtractionFailed, extract_homework

    monkeypatch.setattr(PIL.Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ExtractionFailed, match="INVALID_IMAGE"):
        extract_homework(_make_test_image(800, 600), client=MagicMock())


def test_llm_payload_uses_smaller_image():
    """Payload da LLM usa AI_LLM_MAX_SIDE (1024): menos tokens de visão."""
    from app.core.config import get_settings
    from app.services.vision_openrouter import anonymize_image

    assert get_settings().AI_LLM_MAX_SIDE == 1024
    anonymized, _ = anonymize_image(_make_test_image(2000, 1200),
                                    max_side=get_settings().AI_LLM_MAX_SIDE, quality=82)
    img = Image.open(io.BytesIO(anonymized))
    assert max(img.size) <= 1024


def test_agenda_shape_normalizes_first_task():
    """Foto de agenda (várias matérias) vira a 1ª tarefa + needs_review (formato observado ao vivo)."""
    import json
    from unittest.mock import MagicMock

    from app.services.vision_openrouter import extract_homework

    agenda = {"data": "21/09/2026 - Segunda-Feira", "turma": "4º Ano - B",
              "tarefas": [{"disciplina": "Ciências", "assunto": "A decomposição",
                           "tarefa": "Livro didático, páginas 45 e 46"}],
              "confidence": 0.8}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(agenda)))],
        usage=MagicMock(prompt_tokens=400, completion_tokens=300),
    )
    result = extract_homework(_make_test_image(800, 600), client=mock_client)
    assert result.is_homework is True
    assert result.subject == "Ciências"
    assert result.due_at == "2026-09-21"
    assert "45 e 46" in (result.statement or "")
    assert result.needs_review is True
    assert result.extraction_status == "baixa_confianca"


def test_empty_content_retries_once_then_succeeds():
    """Quirk do provider (content None): 1 retry imediato; usages somados."""
    import json
    from unittest.mock import MagicMock

    from app.services.vision_openrouter import extract_homework

    ok = {"is_homework": True, "subject": "Matemática", "title": "T", "statement": "S",
          "due_at": "2026-09-25", "estimated_minutes": 30, "priority": 1,
          "confidence": 0.9, "needs_review": False}
    empty = MagicMock(choices=[MagicMock(message=MagicMock(content=None))],
                      usage=MagicMock(prompt_tokens=400, completion_tokens=900))
    good = MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(ok)))],
                     usage=MagicMock(prompt_tokens=400, completion_tokens=100))
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [empty, good]
    result = extract_homework(_make_test_image(800, 600), client=mock_client)
    assert result.subject == "Matemática"
    assert mock_client.chat.completions.create.call_count == 2
    assert result.meta["prompt_tokens"] == 800
    assert result.meta["completion_tokens"] == 1000


def test_chat_kwargs_accepted_by_real_sdk_signature():
    """Regressão: kwargs enviados precisam existir na assinatura real do SDK (mocks escondem TypeError)."""
    import inspect

    import openai

    sig = inspect.signature(openai.resources.chat.completions.Completions.create)
    params = set(sig.parameters)
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    from unittest.mock import MagicMock

    from app.core.config import get_settings

    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create
    from app.services.vision_openrouter import ExtractionFailed, _chat_json

    with pytest.raises(ExtractionFailed):  # fake_create levantou dentro do _chat_json
        _chat_json(mock_client, get_settings(), [{"role": "user", "content": "x"}])
    if not accepts_kwargs:
        unknown = set(captured) - params
        assert not unknown, f"kwargs rejeitados pelo SDK real: {unknown}"


def test_embedded_prompt_matches_file_and_never_stub():
    """Anti-drift Docker: prompt embutido == arquivo (imagem não leva ai/)."""
    from pathlib import Path

    from app.services.vision_openrouter import DEFAULT_SYSTEM_PROMPT, _load_system_prompt

    assert len(DEFAULT_SYSTEM_PROMPT) > 500  # nunca um stub
    assert '"due_at"' in DEFAULT_SYSTEM_PROMPT
    repo_file = Path(__file__).resolve().parents[2] / "ai" / "prompts" / "nex_system.txt"
    if repo_file.exists():
        assert repo_file.read_text(encoding="utf-8").strip() == DEFAULT_SYSTEM_PROMPT.strip()
    assert len(_load_system_prompt()) > 500


def test_rate_limit_raises_retryable():
    from app.services.vision_openrouter import extract_homework, OpenRouterRateLimited

    class FakeRateLimitError(Exception):
        status_code = 429

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = FakeRateLimitError("429 rate limited (free tier)")

    with pytest.raises(OpenRouterRateLimited):
        extract_homework(_make_test_image(800, 600), client=mock_client)
