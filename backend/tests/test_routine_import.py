"""TDD RED — Importação de rotina por inferência (texto/imagem → grade+atividades+disponibilidade).

Regras:
- POST /children/{id}/routine/import aceita text OU file (ao menos um; 400 se nenhum)
- LLM extrai schedules/activities/availability; inválidos → 400 sem persistir nada
- replace=false anexa; replace=true substitui grade e disponibilidade
- dono apenas (403 cross-account); due: motor prioriza janelas com responsável
"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from unittest.mock import patch

from tests.conftest import make_auth


@pytest.fixture(autouse=True)
def _clean():
    from app.tasks import routine as R
    from app.tasks.extract import clear_store

    R.clear_routine()
    clear_store()
    yield
    R.clear_routine()
    clear_store()


def _client():
    from app.main import app

    return TestClient(app)


def _auth(c):
    h, _ = make_auth(c)
    cid = c.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    return h, cid


def _fake_routine(**over):
    from app.schemas.routine import RoutineExtractionResult

    base = dict(
        schedules=[{"weekday": 0, "start_time": "07:30", "end_time": "12:00",
                    "subject": "Aula", "kind": "aula"}],
        activities=[{"title": "Natação", "weekday": 2, "start_time": "17:00",
                     "end_time": "18:00", "recurrence": "weekly",
                     "travel_before_min": 20, "travel_after_min": 0, "is_blocking": True}],
        availability=[{"weekday": 0, "start_time": "18:00", "end_time": "20:00"},
                      {"weekday": 2, "start_time": "18:00", "end_time": "20:00"}],
        confidence=0.9, needs_review=False, warnings=[],
    )
    base.update(over)
    return RoutineExtractionResult(**base)


def test_text_import_creates_routine():
    c = _client()
    h, cid = _auth(c)
    with patch("app.services.vision_openrouter.extract_routine", return_value=_fake_routine()):
        r = c.post(f"/v1/children/{cid}/routine/import",
                   data={"text": "Aula seg 07:30-12:00. Natação qua 17-18. Posso acompanhar seg e qua 18-20h."},
                   headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["schedules_created"] == 1
    assert body["activities_created"] == 1
    assert body["availability_saved"] == 2
    ag = c.get(f"/v1/children/{cid}/agenda", headers=h).json()
    assert len(ag["schedules"]) == 1
    assert any(a["title"] == "Natação" for a in ag["activities"])
    assert len(ag["availability"]) == 2


def test_image_import_creates_routine():
    c = _client()
    h, cid = _auth(c)
    img = Image.new("RGB", (800, 600), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    with patch("app.services.vision_openrouter.extract_routine", return_value=_fake_routine()):
        r = c.post(f"/v1/children/{cid}/routine/import",
                   files={"file": (f"{uuid.uuid4()}.jpg", buf.getvalue(), "image/jpeg")},
                   headers=h)
    assert r.status_code == 201, r.text


def test_import_requires_text_or_file():
    c = _client()
    h, cid = _auth(c)
    r = c.post(f"/v1/children/{cid}/routine/import", headers=h)
    assert r.status_code == 400


def test_invalid_entries_rejected_without_persisting():
    import json

    from unittest.mock import MagicMock

    c = _client()
    h, cid = _auth(c)
    # lixo do LLM passa pela coerção real: tudo descartado em warnings
    garbage = json.dumps({
        "schedules": [{"weekday": 9, "start_time": "25:00",
                       "end_time": "10:00", "subject": "X", "kind": "aula"}],
        "activities": [], "availability": [],
        "confidence": 0.1, "needs_review": True, "warnings": [],
    })
    fake_resp = MagicMock(choices=[MagicMock(message=MagicMock(content=garbage))],
                          usage=MagicMock(prompt_tokens=10, completion_tokens=10))
    with patch("app.services.vision_openrouter._chat_json", return_value=fake_resp):
        r = c.post(f"/v1/children/{cid}/routine/import", data={"text": "lixo"}, headers=h)
    assert r.status_code == 400
    ag = c.get(f"/v1/children/{cid}/agenda", headers=h).json()
    assert ag["schedules"] == [] and ag["activities"] == []


def test_model_abstention_returns_422_retryable():
    from app.schemas.routine import RoutineExtractionResult

    c = _client()
    h, cid = _auth(c)
    empty = RoutineExtractionResult(schedules=[], activities=[], availability=[],
                                    confidence=0.0, needs_review=True, warnings=[])
    with patch("app.services.vision_openrouter.extract_routine", return_value=empty):
        r = c.post(f"/v1/children/{cid}/routine/import", data={"text": "x"}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["details"].get("retryable") is True


def test_weekday_list_expands_to_multiple_entries():
    import json

    from unittest.mock import MagicMock

    c = _client()
    h, cid = _auth(c)
    payload = json.dumps({
        "schedules": [{"weekday": [0, 1, 2, 3, 4], "start_time": "07:30",
                       "end_time": "12:00", "subject": "Aula", "kind": "aula"}],
        "activities": [], "availability": [],
        "confidence": 0.9, "needs_review": False, "warnings": [],
    })
    fake_resp = MagicMock(choices=[MagicMock(message=MagicMock(content=payload))],
                          usage=MagicMock(prompt_tokens=10, completion_tokens=10))
    with patch("app.services.vision_openrouter._chat_json", return_value=fake_resp):
        r = c.post(f"/v1/children/{cid}/routine/import", data={"text": "aula seg a sex"}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["schedules_created"] == 5


def test_import_forbidden_cross_account():
    c = _client()
    h, cid = _auth(c)
    h2, _ = make_auth(c, name="Outro")
    with patch("app.services.vision_openrouter.extract_routine", return_value=_fake_routine()):
        r = c.post(f"/v1/children/{cid}/routine/import", data={"text": "x"}, headers=h2)
    assert r.status_code == 403


def test_engine_prefers_parent_available_slots():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.scheduling import suggest_slots

    tz = ZoneInfo("America/Sao_Paulo")
    hw = {"due_at": datetime(2026, 9, 25, 23, 59, tzinfo=tz),
          "estimated_minutes": 30, "priority": 1, "subject": "Matemática"}
    # 22/09/2026 é terça (weekday 1)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=tz)
    s0 = suggest_slots(hw, now=now, limit=5)
    assert s0 and s0[0]["start_at"].hour == 14  # foco da tarde vence sem bônus
    avail = [{"weekday": 1, "start_time": "19:00", "end_time": "20:00"}]
    s1 = suggest_slots(hw, now=now, limit=5, availability=avail)
    assert s1 and s1[0]["start_at"].hour == 19  # bônus do responsável vira o jogo
    assert "responsável disponível" in s1[0]["reason"]
