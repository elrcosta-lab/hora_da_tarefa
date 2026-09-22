"""TDD RED — Revisão humana da extração (RF-06, PRD §4 RF-06).

Regras:
- PATCH /homeworks/{id} edita subject/title/statement/due_at/estimated_minutes/priority
  (dono apenas; 403 cross-account; 400 em data inválida)
- editar após extração marca reviewed (needs_review=false quando críticos presentes)
- fluxo: processando → editável → accept agenda (sugestões refletem edição)
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
    from app.tasks.extract import clear_store

    clear_store()
    yield
    clear_store()


def _img_bytes(seed=0):
    img = Image.new("RGB", (800 + seed, 600), (60, 70, 80))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mk_result(**over):
    from app.schemas.extraction import ExtractionResult

    base = dict(is_homework=True, subject=None, title="Ilegível", statement="?",
                due_at=None, estimated_minutes=None, priority=1, confidence=0.42,
                needs_review=True, extraction_status="baixa_confianca", meta={})
    base.update(over)
    return ExtractionResult(**base)


def _setup(client):
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    return h, cid


def _upload(client, h, cid, seed=0, **over):
    with patch("app.services.vision_openrouter.extract_homework", return_value=_mk_result(**over)):
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(seed), "image/jpeg")},
                        data={"child_id": cid}, headers=h)
    assert r.status_code == 202, r.text
    return r.json()["homework_id"]


def test_low_confidence_task_is_editable_and_review_clears():
    from app.main import app

    client = TestClient(app)
    h, cid = _setup(client)
    hid = _upload(client, h, cid, seed=1)
    d0 = client.get(f"/v1/homeworks/{hid}", headers=h).json()
    assert d0["extraction_status"] == "baixa_confianca"

    r = client.patch(f"/v1/homeworks/{hid}", json={
        "subject": "Matemática", "title": "Lista de frações",
        "statement": "Ex 1 a 10, pág. 42", "due_at": "2026-09-25",
        "estimated_minutes": 40, "priority": 2,
    }, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subject"] == "Matemática"
    assert body["needs_review"] is False

    # sugestões passam a refletir a correção
    s = client.get("/v1/suggestions", params={"homework_id": hid}, headers=h).json()["suggestions"]
    assert len(s) > 0


def test_patch_validates_and_scopes():
    from app.main import app

    client = TestClient(app)
    h, cid = _setup(client)
    hid = _upload(client, h, cid, seed=2)

    r = client.patch(f"/v1/homeworks/{hid}", json={"due_at": "31/02/2030"}, headers=h)
    assert r.status_code == 400
    r = client.patch(f"/v1/homeworks/{hid}", json={"priority": 9}, headers=h)
    assert r.status_code == 400
    r = client.patch(f"/v1/homeworks/{uuid.uuid4()}", json={"title": "X"}, headers=h)
    assert r.status_code == 404

    h2, _ = make_auth(client, name="Outro")
    r = client.patch(f"/v1/homeworks/{hid}", json={"title": "X"}, headers=h2)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
