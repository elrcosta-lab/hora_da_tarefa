"""TDD RED — Beat agendado (SPECS §1.2 scheduler, §6.4 retry, PRD RF-10).

Regras:
- tick único: mark_overdue + dispatch_due + (purge 1x/dia fica no scheduler)
- falha de envio por destinatário: attempts++, erro registrado; 3ª falha → failed
- falha de um não derruba os demais nem o tick
"""
import io
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from unittest.mock import patch

from tests.conftest import make_auth

TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _clean():
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


def _jpeg():
    img = Image.new("RGB", (800, 600), (7, 8, 9))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_overdue_homework(client, user_id, due="2020-01-01"):
    from app.schemas.extraction import ExtractionResult

    c = client
    h, _ = make_auth(c, name="Beat")
    cid = c.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    ok = ExtractionResult(is_homework=True, subject="Mat", title="T", statement="S",
                          due_at=due, estimated_minutes=30, priority=1,
                          confidence=0.9, needs_review=False, extraction_status="ok", meta={})
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        from app.tasks.extract import get_or_create_homework, run_extraction, update_homework_fields

        rec, _ = get_or_create_homework(_jpeg(), child_id=cid, created_by_user_id=user_id)
        run_extraction(rec["homework_id"])
    # data passada só entra por edição manual (extração rejeita); simula tarefa vencida
    if due and due < "2026-01-01":
        update_homework_fields(rec["homework_id"], due_at=due)
    return rec["homework_id"]


def test_tick_marks_overdue_and_dispatches():
    from app.tasks import beat as B
    from app.tasks import users as U
    from app.tasks.extract import get_homework

    c = _client()
    u = U.create_user("Pai")
    U.link_telegram(u["link_code"], 701)
    hid = _make_overdue_homework(c, u["user_id"])

    delivered = []
    out = B.run_beat_tick(now=datetime(2026, 9, 26, 12, 0, tzinfo=TZ),
                          sender=lambda chat, text: delivered.append((chat, text)))
    assert hid in out["overdue"]
    assert get_homework(hid)["status"] == "atrasada"
    assert out["sent"] >= 1
    assert all(chat == 701 for chat, _ in delivered)


def test_sender_failure_retries_then_fails_without_killing_tick():
    from app.tasks import beat as B
    from app.tasks import notify as N
    from app.tasks import users as U

    c = _client()
    u = U.create_user("Pai")
    U.link_telegram(u["link_code"], 702)
    _make_overdue_homework(c, u["user_id"])

    def boom(chat, text):
        raise RuntimeError("Bot API fora")

    now = datetime(2026, 9, 26, 12, 0, tzinfo=TZ)
    for i in range(3):
        out = B.run_beat_tick(now=now, sender=boom)
        assert out["errors"] >= 1
    recs = N.list_notifications()
    pendentes = [r for r in recs if r["status"] == "scheduled"]
    falhadas = [r for r in recs if r["status"] == "failed"]
    assert not pendentes or falhadas  # após retries, viram failed
    assert all(r["attempts"] >= 1 for r in falhadas)
    # tick seguinte não tenta failed de novo
    out2 = B.run_beat_tick(now=now, sender=boom)
    assert out2["errors"] == 0


def test_tick_without_sender_only_marks():
    from app.tasks import beat as B

    out = B.run_beat_tick(now=datetime(2026, 9, 26, 12, 0, tzinfo=TZ))
    assert out["overdue"] == []
    assert out["sent"] == 0
    assert out["errors"] == 0


def test_select_sender_polling_queues_outbox(monkeypatch):
    """O1: em modo polling (VPS), o beat deve enfileirar no outbox — nunca None.
    sender=None marca sent sem entregar (lembretes morriam em silêncio)."""
    monkeypatch.setenv("TELEGRAM_LIVE_SEND", "false")
    monkeypatch.setenv("TELEGRAM_POLLING", "true")
    from app.bot.handlers import last_sent
    from app.core.config import get_settings
    from app.tasks import beat as B

    sender = B._select_sender(get_settings())
    assert sender is not None
    sender(701, "lembrete de teste")
    assert last_sent(701) == "lembrete de teste"


def test_select_sender_neither_is_none(monkeypatch):
    monkeypatch.setenv("TELEGRAM_LIVE_SEND", "false")
    monkeypatch.setenv("TELEGRAM_POLLING", "false")
    from app.core.config import get_settings
    from app.tasks import beat as B

    assert B._select_sender(get_settings()) is None


def test_beat_tick_polling_sender_delivers_due():
    """Integração: lembrete vencido chega ao outbox do chat vinculado."""
    from app.bot.handlers import last_sent
    from app.tasks import beat as B
    from app.tasks import users as U

    c = _client()
    u = U.create_user("Pai")
    U.link_telegram(u["link_code"], 703)
    hid = _make_overdue_homework(c, u["user_id"])

    import os

    os.environ["TELEGRAM_LIVE_SEND"] = "false"
    os.environ["TELEGRAM_POLLING"] = "true"
    try:
        from app.core.config import get_settings

        out = B.run_beat_tick(now=datetime(2026, 9, 26, 12, 0, tzinfo=TZ),
                              sender=B._select_sender(get_settings()))
    finally:
        os.environ.pop("TELEGRAM_LIVE_SEND", None)
        os.environ.pop("TELEGRAM_POLLING", None)
    assert out["sent"] >= 1
    assert last_sent(703) is not None
    _ = hid
