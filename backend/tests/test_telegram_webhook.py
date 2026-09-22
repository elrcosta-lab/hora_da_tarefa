"""Bot Telegram webhook (SPECS §6 + §3.10, RF-08) — ACESSO RESTRITO a vinculados.

- POST /v1/telegram/webhook exige secret (401 se inválido)
- idempotência por update_id; sem vínculo → mensagem restrita, nada criado
- chats dos testes são vinculados via app.tasks.users antes de cada cenário
"""
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    from app.tasks import routine as R
    from app.tasks import users as U
    from app.tasks.extract import clear_store
    from app.bot import handlers as H

    R.clear_routine()
    U.clear_users()
    clear_store()
    H.clear_bot()
    yield
    R.clear_routine()
    U.clear_users()
    clear_store()
    H.clear_bot()


def _link(chat_id: int, name: str = "Teste") -> dict:
    """Vincula o chat id diretamente (atalho de teste p/ o fluxo de código)."""
    from app.tasks import users as U

    u = U.create_user(name)
    code = u["link_code"]
    linked = U.link_telegram(code, chat_id)
    assert linked is not None
    return linked


def _client():
    from app.main import app

    return TestClient(app)


def _headers():
    return {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}


def test_webhook_rejects_invalid_secret():
    c = _client()
    r = c.post("/v1/telegram/webhook",
               headers={"X-Telegram-Bot-Api-Secret-Token": "errado"},
               json={"update_id": 1, "message": {"text": "/start"}})
    assert r.status_code == 401


def test_start_returns_welcome_with_link_code():
    c = _client()
    r = c.post("/v1/telegram/webhook", headers=_headers(), json={
        "update_id": 10,
        "message": {"message_id": 1, "from": {"id": 123}, "chat": {"id": 123}, "text": "/start"},
    })
    assert r.status_code == 200
    from app.bot.handlers import last_sent

    sent = last_sent(123)
    assert sent is not None and "Hora da Tarefa" in sent
    assert "código" in sent.lower()


def test_photo_upload_creates_homework_and_replies_processando():
    _link(7)
    c = _client()
    import base64
    import io

    from PIL import Image

    img = Image.new("RGB", (800, 600), (9, 9, 9))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    r = c.post("/v1/telegram/webhook", headers=_headers(), json={
        "update_id": 20,
        "message": {"message_id": 2, "from": {"id": 7}, "chat": {"id": 7},
                    "photo": [{"file_id": "abc", "width": 800, "height": 600}],
                    "caption": "é de matemática",
                    "test_bytes_b64": b64},
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    from app.bot.handlers import last_sent
    from app.tasks.extract import list_homeworks

    assert len(list_homeworks()) == 1
    assert "processando" in last_sent(7).lower()


def test_dedupe_same_update_id_ignored():
    _link(9)
    c = _client()
    payload = {"update_id": 30,
               "message": {"message_id": 3, "from": {"id": 9}, "chat": {"id": 9}, "text": "/hoje"}}
    r1 = c.post("/v1/telegram/webhook", headers=_headers(), json=payload)
    r2 = c.post("/v1/telegram/webhook", headers=_headers(), json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json().get("deduplicated") is True
    from app.bot.handlers import sent_count

    # /hoje envia 1 mensagem na primeira vez, zero na repetição
    assert sent_count(9) == 1


def test_concluir_command_updates_status():
    _link(11)
    c = _client()
    from app.tasks import routine as R
    from app.tasks.extract import get_homework

    cid = R.create_child("Ana")["id"]
    # cria tarefa via foto simulada com bytes reais
    import io
    from PIL import Image
    from unittest.mock import patch
    from app.schemas.extraction import ExtractionResult

    img = Image.new("RGB", (800, 600), (5, 6, 7))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    ok = ExtractionResult(is_homework=True, subject="Mat", title="T", statement="S",
                          due_at="2026-09-25", estimated_minutes=30, priority=1,
                          confidence=0.9, needs_review=False, extraction_status="ok", meta={})
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        up = c.post("/v1/homeworks/upload", files={"file": ("t.jpg", buf.getvalue(), "image/jpeg")},
                    data={"child_id": cid})
    hid = up.json()["homework_id"]
    r = c.post("/v1/telegram/webhook", headers=_headers(), json={
        "update_id": 40,
        "message": {"message_id": 4, "from": {"id": 11}, "chat": {"id": 11}, "text": f"/concluir {hid[:8]}"},
    })
    assert r.status_code == 200
    # prefixo de 8 chars deve resolver a tarefa (atalho do bot)
    assert get_homework(hid)["status"] in ("concluida", "em_andamento", "agendada")
