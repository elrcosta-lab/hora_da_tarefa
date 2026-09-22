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


def _upload_with_due(client, child_id, due="2026-09-25", minutes=40):
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
                        data={"child_id": child_id})
    return r.json()["homework_id"]


def test_schedule_creates_24h_and_2h():
    from app.tasks import notify as N

    c = _client()
    cid = c.post("/v1/children", json={"name": "Ana"}).json()["id"]
    hid = _upload_with_due(c, cid, due="2026-09-25")
    recs = N.list_notifications(homework_id=hid)
    kinds = {r["kind"] for r in recs}
    assert {"lembrete_24h", "lembrete_2h"} <= kinds
    # idempotency keys únicas
    keys = [r["idempotency_key"] for r in recs]
    assert len(keys) == len(set(keys))


def test_dispatch_sends_once_even_if_run_twice():
    from app.tasks import notify as N

    c = _client()
    cid = c.post("/v1/children", json={"name": "Ana"}).json()["id"]
    hid = _upload_with_due(c, cid, due="2026-09-25")
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
    cid = c.post("/v1/children", json={"name": "Ana"}).json()["id"]
    # due 2026-09-25 23:59 → 2h antes = 21:59 (dentro da janela, ok);
    # due 2026-09-26 01:00 → 2h antes = 23:00 (quiet) → deve ir p/ 07:00 do dia 26
    hid = _upload_with_due(c, cid, due="2026-09-26")
    from app.tasks.extract import update_homework_fields

    update_homework_fields(hid, due_at="2026-09-26T01:00:00-03:00")
    N.clear_notifications()
    N.schedule_for_homework(hid)
    recs = {r["kind"]: r for r in N.list_notifications(homework_id=hid)}
    h = recs["lembrete_2h"]["scheduled_for"].hour
    assert h == 7


def test_atraso_scheduled_when_overdue_and_list_endpoint():
    from app.tasks import notify as N

    c = _client()
    cid = c.post("/v1/children", json={"name": "Ana"}).json()["id"]
    hid = _upload_with_due(c, cid, due="2020-01-01")
    N.schedule_for_homework(hid)
    kinds = {r["kind"] for r in N.list_notifications(homework_id=hid)}
    assert "atraso" in kinds
    r = c.get("/v1/notifications", params={"homework_id": hid})
    assert r.status_code == 200
    assert r.json()["total"] >= 1
