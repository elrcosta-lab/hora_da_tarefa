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
        from app.tasks.extract import get_or_create_homework, run_extraction

        rec, _ = get_or_create_homework(_jpeg(), child_id=cid, created_by_user_id=user_id)
        run_extraction(rec["homework_id"])
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
