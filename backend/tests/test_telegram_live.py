"""TDD RED — Telegram real: getFile download + sendMessage + dispatch p/ dono.

Regras:
- foto sem bytes de teste baixa via getFile (falha → msg de erro, nada criado)
- flush do outbox p/ Bot API só com TELEGRAM_LIVE_SEND=true (testes usam outbox)
- dispatch_due(sender=) entrega ao created_by_user_id vinculado, uma única vez
"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_LIVE_SEND", "false")
    from app.tasks import routine as R
    from app.tasks import users as U
    from app.tasks.extract import clear_store
    from app.tasks import notify as N
    from app.bot import handlers as H

    R.clear_routine()
    U.clear_users()
    clear_store()
    N.clear_notifications()
    H.clear_bot()
    yield
    R.clear_routine()
    U.clear_users()
    clear_store()
    N.clear_notifications()
    H.clear_bot()


def _client():
    from app.main import app

    return TestClient(app)


def _headers():
    return {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}


def _link(chat_id: int):
    from app.tasks import users as U

    u = U.create_user("Mae")
    assert U.link_telegram(u["link_code"], chat_id) is not None


def _jpeg(color=(1, 2, 3)):
    img = Image.new("RGB", (800, 600), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_photo_download_failure_creates_nothing():
    _link(601)
    c = _client()
    from app.bot.handlers import last_sent
    from app.tasks.extract import list_homeworks

    with patch("app.bot.telegram_api.download_photo", side_effect=RuntimeError("getFile 404")):
        r = c.post("/v1/telegram/webhook", headers=_headers(), json={
            "update_id": 200,
            "message": {"message_id": 1, "from": {"id": 601}, "chat": {"id": 601},
                        "photo": [{"file_id": "bad"}]},
        })
    assert r.status_code == 200
    assert list_homeworks() == []
    assert "baixar" in (last_sent(601) or "").lower()


def test_photo_download_success_creates_homework_and_owner():
    _link(602)
    c = _client()
    from app.tasks.extract import list_homeworks

    with patch("app.bot.telegram_api.download_photo", return_value=_jpeg()):
        r = c.post("/v1/telegram/webhook", headers=_headers(), json={
            "update_id": 201,
            "message": {"message_id": 1, "from": {"id": 602}, "chat": {"id": 602},
                        "photo": [{"file_id": "good"}]},
        })
    assert r.status_code == 200
    assert len(list_homeworks()) == 1


def test_live_flush_calls_send_message(monkeypatch):
    import app.bot.handlers as H

    _link(603)
    c = _client()
    monkeypatch.setenv("TELEGRAM_LIVE_SEND", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:AA-fake-live-token")
    calls = []

    def fake_send(token, chat_id, text):
        calls.append((chat_id, text))
        return 999

    monkeypatch.setattr("app.bot.telegram_api.send_message", fake_send)
    r = c.post("/v1/telegram/webhook", headers=_headers(), json={
        "update_id": 202,
        "message": {"message_id": 1, "from": {"id": 603}, "chat": {"id": 603}, "text": "/hoje"},
    })
    assert r.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == 603


def test_dispatch_delivers_to_linked_owner_once():
    from app.schemas.extraction import ExtractionResult
    from app.tasks import notify as N
    from app.tasks import users as U

    _link(604)
    c = _client()
    u = U.get_by_telegram_id(604)
    from app.tasks import routine as R

    cid = R.create_child("Ana")["id"]
    ok = ExtractionResult(is_homework=True, subject="Mat", title="Lista de frações",
                          statement="S", due_at="2026-09-25", estimated_minutes=30,
                          priority=1, confidence=0.9, needs_review=False,
                          extraction_status="ok", meta={})
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        from app.tasks.extract import get_or_create_homework, run_extraction

        rec, _ = get_or_create_homework(_jpeg(), child_id=cid, created_by_user_id=u["user_id"])
        run_extraction(rec["homework_id"])

    sent_texts = []
    from datetime import datetime
    from zoneinfo import ZoneInfo

    N.dispatch_due(now=datetime(2026, 9, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
                   sender=lambda chat_id, text: sent_texts.append((chat_id, text)))
    assert len(sent_texts) >= 2
    assert all(chat == 604 for chat, _ in sent_texts)
    assert any("Lista de frações" in t or "Mat" in t for _, t in sent_texts)
    # segunda passada não reenvia
    N.dispatch_due(now=datetime(2026, 9, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
                   sender=lambda chat_id, text: sent_texts.append((chat_id, text)))
    assert len(sent_texts) >= 2
    n_before = len(sent_texts)
    N.dispatch_due(now=datetime(2026, 9, 27, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
                   sender=lambda chat_id, text: sent_texts.append((chat_id, text)))
    assert len(sent_texts) == n_before
