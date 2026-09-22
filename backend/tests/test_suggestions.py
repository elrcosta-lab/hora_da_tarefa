"""TDD RED — GET /suggestions + accept (SPECS §3.8 §3.9, RF-05/RF-06)."""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _clean():
    from app.tasks.extract import clear_store

    clear_store()
    yield
    clear_store()


def _img_bytes(seed=1):
    img = Image.new("RGB", (800 + seed, 600), (120 + seed, 160, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _upload_ok(client, seed=1):
    from app.schemas.extraction import ExtractionResult

    ok = ExtractionResult(
        is_homework=True, subject="Matemática", title="Lista", statement="Ex 1-8",
        due_at="2026-09-25", estimated_minutes=40, priority=2,
        confidence=0.9, needs_review=False, extraction_status="ok", meta={},
    )
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        r = client.post(
            "/v1/homeworks/upload",
            files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(seed), "image/jpeg")},
            data={"child_id": str(uuid.uuid4())},
        )
    assert r.status_code == 202
    return r.json()["homework_id"]


def test_get_suggestions_ranked():
    from app.main import app

    client = TestClient(app)
    hid = _upload_ok(client, seed=5)
    r = client.get("/v1/suggestions", params={"homework_id": hid, "limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["homework_id"] == hid
    assert 1 <= len(body["suggestions"]) <= 5
    scores = [s["score"] for s in body["suggestions"]]
    assert scores == sorted(scores, reverse=True)
    assert body["suggestions"][0]["rank"] == 1


def test_accept_suggestion_schedules_homework():
    from app.main import app

    client = TestClient(app)
    hid = _upload_ok(client, seed=9)
    sug = client.get("/v1/suggestions", params={"homework_id": hid, "limit": 3}).json()["suggestions"]
    start_at = sug[0]["start_at"]
    r = client.post(f"/v1/homeworks/{hid}/accept", json={"start_at": start_at})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "agendada"
    detail = client.get(f"/v1/homeworks/{hid}").json()
    assert detail["status"] == "agendada"


def test_accept_invalid_slot_returns_409():
    from app.main import app

    client = TestClient(app)
    hid = _upload_ok(client, seed=13)
    r = client.post(f"/v1/homeworks/{hid}/accept", json={"start_at": "2020-01-01T00:00:00-03:00"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "STATUS_CONFLICT"
