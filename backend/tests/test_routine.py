"""TDD RED — Grade/atividades CRUD + agenda (SPECS §2.5 §2.6 §2.7 §3.6 §3.7 §3.11, RF-08).

Regras: aula não sobrepõe outra (mesma criança/dia), 30min–8h; atividade gera
bloco com deslocamento; suggestions passam a respeitar a rotina real.
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


def _client():
    from app.main import app

    return TestClient(app)


def _auth(c):
    h, _ = make_auth(c)
    return h


def test_create_and_list_children():
    c = _client()
    h = _auth(c)
    r = c.post("/v1/children", json={"name": "Ana", "grade_level": "4º ano"}, headers=h)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    lst = c.get("/v1/children", headers=h).json()
    assert any(x["id"] == cid for x in lst["items"])


def test_schedules_reject_overlap():
    c = _client()
    h = _auth(c)
    cid = c.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    ok = c.post(f"/v1/children/{cid}/schedules", json={
        "replace": True,
        "entries": [
            {"weekday": 1, "start_time": "07:30", "end_time": "08:20", "subject": "Matemática"},
            {"weekday": 1, "start_time": "08:20", "end_time": "09:10", "subject": "Português"},
        ],
    }, headers=h)
    assert ok.status_code == 201
    bad = c.post(f"/v1/children/{cid}/schedules", json={
        "replace": False,
        "entries": [{"weekday": 1, "start_time": "08:00", "end_time": "09:00", "subject": "Choque"}],
    }, headers=h)
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "SCHEDULE_OVERLAP"


def test_schedules_reject_too_short():
    c = _client()
    h = _auth(c)
    cid = c.post("/v1/children", json={"name": "Beto"}, headers=h).json()["id"]
    r = c.post(f"/v1/children/{cid}/schedules", json={
        "entries": [{"weekday": 2, "start_time": "10:00", "end_time": "10:10", "subject": "X"}],
    }, headers=h)
    assert r.status_code == 400


def test_activities_and_agenda():
    c = _client()
    h = _auth(c)
    cid = c.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    a = c.post(f"/v1/children/{cid}/activities", json={
        "title": "Natação", "weekday": 3, "start_time": "17:00", "end_time": "18:00",
        "recurrence": "weekly", "travel_before_min": 20, "is_blocking": True,
    }, headers=h)
    assert a.status_code == 201, a.text
    ag = c.get(f"/v1/children/{cid}/agenda", headers=h)
    assert ag.status_code == 200
    assert len(ag.json()["activities"]) == 1
    assert ag.json()["activities"][0]["title"] == "Natação"


def test_suggestions_respect_real_routine():
    from app.schemas.extraction import ExtractionResult

    c = _client()
    h = _auth(c)
    cid = c.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    ok = ExtractionResult(
        is_homework=True, subject="Matemática", title="Lista", statement="Ex",
        due_at="2026-09-25", estimated_minutes=40, priority=2,
        confidence=0.9, needs_review=False, extraction_status="ok", meta={},
    )
    img = Image.new("RGB", (800, 600), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        hid = c.post("/v1/homeworks/upload",
            files={"file": ("t.jpg", buf.getvalue(), "image/jpeg")},
            data={"child_id": cid}, headers=h).json()["homework_id"]
    free = c.get("/v1/suggestions", params={"homework_id": hid, "limit": 5}, headers=h).json()["suggestions"]
    assert len(free) > 0
    # bloqueia toda a janela de estudo da semana com aula → sugestões caem
    c.post(f"/v1/children/{cid}/schedules", json={
        "replace": True,
        "entries": [
            {"weekday": d, "start_time": "14:00", "end_time": "21:00", "subject": "Bloqueio"}
            for d in range(7)
        ],
    }, headers=h)
    blocked = c.get("/v1/suggestions", params={"homework_id": hid, "limit": 5}, headers=h).json()["suggestions"]
    # tardes bloqueadas a semana toda: sugestões migram para as manhãs livres, sem colidir
    assert len(blocked) > 0
    for s in blocked:
        h0 = int(s["start_at"][11:13])
        assert not (14 <= h0 < 21)
    assert blocked[0]["start_at"][11:13] < "14"
