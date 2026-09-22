"""TDD RED — FSM de status PATCH (SPECS §4.6 + §3.5 v1.1, PRD RF-09).

Matriz (SPECS §4.6):
pendente → agendada | em_andamento | cancelada | atrasada(auto)
agendada → em_andamento | cancelada | atrasada(auto)
em_andamento → concluida | nao_realizada
atrasada → concluida | nao_realizada | cancelada
concluida|nao_realizada|cancelada → arquivada
arquivada → (terminal)
Fora da matriz → 409 STATUS_CONFLICT.
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
    img = Image.new("RGB", (800 + seed, 600), (100 + seed, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _auth(client):
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    return h, cid


def _upload(client, headers=None, child_id=None):
    from app.schemas.extraction import ExtractionResult

    h = headers or _auth(client)[0]
    if child_id is None:
        child_id = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    ok = ExtractionResult(
        is_homework=True, subject="Matemática", title="T", statement="S",
        due_at="2026-09-25", estimated_minutes=30, priority=1,
        confidence=0.9, needs_review=False, extraction_status="ok", meta={},
    )
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        r = client.post(
            "/v1/homeworks/upload",
            files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(seed=uuid.uuid4().int % 200), "image/jpeg")},
            data={"child_id": child_id},
            headers=h,
        )
    assert r.status_code == 202
    return h, r.json()["homework_id"]


def test_valid_transition_pendente_to_agendada():
    from app.main import app

    client = TestClient(app)
    h, hid = _upload(client)
    r = client.patch(f"/v1/homeworks/{hid}/status", json={"status": "agendada"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "agendada"


def test_invalid_transition_concluida_to_pendente_returns_409():
    from app.main import app

    client = TestClient(app)
    h, hid = _upload(client)
    client.patch(f"/v1/homeworks/{hid}/status", json={"status": "agendada"}, headers=h)
    client.patch(f"/v1/homeworks/{hid}/status", json={"status": "em_andamento"}, headers=h)
    client.patch(f"/v1/homeworks/{hid}/status", json={"status": "concluida"}, headers=h)
    r = client.patch(f"/v1/homeworks/{hid}/status", json={"status": "pendente"}, headers=h)
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "STATUS_CONFLICT"
    assert "pendente" not in body["error"]["details"].get("allowed", ["pendente"])


def test_full_lifecycle_to_arquivada():
    from app.main import app

    client = TestClient(app)
    h, hid = _upload(client)
    for st in ["agendada", "em_andamento", "concluida", "arquivada"]:
        r = client.patch(f"/v1/homeworks/{hid}/status", json={"status": st}, headers=h)
        assert r.status_code == 200, f"{st}: {r.text}"
        assert r.json()["status"] == st


def test_beat_marks_overdue_as_atrasada():
    from app.tasks.extract import get_homework, mark_overdue, update_homework_fields

    from app.main import app

    client = TestClient(app)
    h, hid = _upload(client)
    # simula prazo vencido (escreve no banco — dicts são cópias destacadas)
    update_homework_fields(hid, due_at="2020-01-01")
    client.patch(f"/v1/homeworks/{hid}/status", json={"status": "agendada"}, headers=h)
    marked = mark_overdue(now="2026-09-22T10:00:00-03:00")
    assert hid in marked
    assert get_homework(hid)["status"] == "atrasada"
    # concluída nunca volta para atrasada
    client.patch(f"/v1/homeworks/{hid}/status", json={"status": "concluida"}, headers=h)
    marked2 = mark_overdue(now="2026-09-22T10:00:00-03:00")
    assert hid not in marked2
