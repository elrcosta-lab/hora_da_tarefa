"""TDD RED — Lembretes 24h/2h com notification_log (SPECS §2.11 §6.4, PRD RF-10, CA-03).

Regras: dedupe por idempotency_key {kind}:{homework}:{child} (nunca duplica),
quiet 22h–07h empurra p/ 07:00, dispatch duplo envia uma única vez.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
import io
from unittest.mock import patch

from tests.conftest import make_auth

TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _clean():
    from app.tasks import routine as R
    from app.tasks.extract import clear_store
    from app.tasks import notify as N

    R.clear_routine()
    clear_store()
    N.clear_notifications()
    yield
    R.clear_routine()
    clear_store()
    N.clear_notifications()


def _client():
    from app.main import app

    return TestClient(app)


def _upload_with_due(client, headers, child_id, due="2026-09-25", minutes=40):
    from app.schemas.extraction import ExtractionResult

    ok = ExtractionResult(is_homework=True, subject="Mat", title="T", statement="S",
                          due_at=due, estimated_minutes=minutes, priority=1,
                          confidence=0.9, needs_review=False, extraction_status="ok", meta={})
    img = Image.new("RGB", (800, 600), (9, 9, 9))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", buf.getvalue(), "image/jpeg")},
                        data={"child_id": child_id}, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()["homework_id"]


def _setup(client):
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    return h, cid


def test_schedule_creates_24h_and_2h():
    from app.tasks import notify as N

    c = _client()
    h, cid = _setup(c)
    hid = _upload_with_due(c, h, cid, due="2026-09-25")
    recs = N.list_notifications(homework_id=hid)
    kinds = {r["kind"] for r in recs}
    assert {"lembrete_24h", "lembrete_2h"} <= kinds
    # idempotency keys únicas
    keys = [r["idempotency_key"] for r in recs]
    assert len(keys) == len(set(keys))


def test_dispatch_sends_once_even_if_run_twice():
    from app.tasks import notify as N

    c = _client()
    h, cid = _setup(c)
    hid = _upload_with_due(c, h, cid, due="2026-09-25")
    # força vencimento: tudo devido agora
    sent1 = N.dispatch_due(now=datetime(2026, 9, 26, 12, 0, tzinfo=TZ))
    sent2 = N.dispatch_due(now=datetime(2026, 9, 26, 12, 0, tzinfo=TZ))
    assert len(sent1) >= 2
    assert sent2 == []
    # cada key enviada exatamente uma vez
    for r in N.list_notifications(homework_id=hid):
        if r["kind"] in ("lembrete_24h", "lembrete_2h"):
            assert r["status"] == "sent"
            assert r["attempts"] == 1


def test_quiet_hours_pushes_to_0700():
    from app.tasks import notify as N

    c = _client()
    h, cid = _setup(c)
    # due 2026-09-25 23:59 → 2h antes = 21:59 (dentro da janela, ok);
    # due 2026-09-26 01:00 → 2h antes = 23:00 (quiet) → deve ir p/ 07:00 do dia 26
    hid = _upload_with_due(c, h, cid, due="2026-09-26")
    from app.tasks.extract import update_homework_fields

    update_homework_fields(hid, due_at="2026-09-26T01:00:00-03:00")
    N.clear_notifications()
    N.schedule_for_homework(hid)
    recs = {r["kind"]: r for r in N.list_notifications(homework_id=hid)}
    h = recs["lembrete_2h"]["scheduled_for"].hour
    assert h == 7


def test_settings_get_returns_server_state():
    from app.main import app

    client = TestClient(app)
    h, cid = _setup(client)
    r = client.get("/v1/notifications/settings", params={"child_id": cid}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"child_id": cid, "lembrete_24h": True, "lembrete_2h": True,
                        "quiet_start": "21:30", "quiet_end": "07:00"}
    client.post("/v1/notifications/settings",
                json={"child_id": cid, "lembrete_2h": False}, headers=h)
    r = client.get("/v1/notifications/settings", params={"child_id": cid}, headers=h)
    assert r.json()["lembrete_2h"] is False
    h2, _ = make_auth(client, name="Outro")
    assert client.get("/v1/notifications/settings", params={"child_id": cid}, headers=h2).status_code == 403


def test_atraso_scheduled_when_overdue_and_list_endpoint():
    from app.tasks import notify as N

    c = _client()
    h, cid = _setup(c)
    hid = _upload_with_due(c, h, cid, due="2026-09-25")
    from app.tasks.extract import update_homework_fields

    update_homework_fields(hid, due_at="2020-01-01")  # vencida só por edição manual
    N.schedule_for_homework(hid)
    kinds = {r["kind"] for r in N.list_notifications(homework_id=hid)}
    assert "atraso" in kinds
    r = c.get("/v1/notifications", params={"homework_id": hid}, headers=h)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_dispatch_skips_concluded_homework():
    """Bug real (2026-09-24): lembrete_2h enviado de manhã p/ tarefa concluída à noite.

    dispatch_due NÃO deve enviar nada de homework em status terminal;
    pendentes devem ser marcados cancelled. Transição p/ terminal também
    cancela os scheduled restantes (defesa em profundidade).
    """
    from app.tasks import notify as N
    from app.tasks.extract import transition_homework

    c = _client()
    h, cid = _setup(c)
    hid = _upload_with_due(c, h, cid, due="2026-09-25")
    # escoa a FSM até concluída: pendente → em_andamento → concluida
    transition_homework(hid, "em_andamento")
    transition_homework(hid, "concluida")
    # mesmo com tudo vencido, nada deve ser enviado
    sent_log: list = []
    sent = N.dispatch_due(now=datetime(2026, 9, 26, 12, 0, tzinfo=TZ),
                          sender=lambda chat, text: sent_log.append((chat, text)))
    assert sent == []
    assert sent_log == []
    remaining = [r for r in N.list_notifications(homework_id=hid) if r["status"] == "scheduled"]
    assert remaining == []
    # e nada novo deve ser agendado p/ tarefa terminal
    assert N.schedule_for_homework(hid) == []
