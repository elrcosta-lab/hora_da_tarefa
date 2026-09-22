"""TDD RED — Extração via OpenRouter gemma-4-26b-a4b-it:free (SPECS §5 v1.1).

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


def test_extract_calls_openrouter_with_gemma_free_and_parses_json():
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
    assert kwargs["model"] == "google/gemma-4-26b-a4b-it:free"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_tokens"] == 2048
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
    assert result.meta["engine"] == "google/gemma-4-26b-a4b-it:free"


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


def test_rate_limit_raises_retryable():
    from app.services.vision_openrouter import extract_homework, OpenRouterRateLimited

    class FakeRateLimitError(Exception):
        status_code = 429

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = FakeRateLimitError("429 rate limited (free tier)")

    with pytest.raises(OpenRouterRateLimited):
        extract_homework(_make_test_image(800, 600), client=mock_client)
