"""TDD RED — GET imagem da tarefa (P1 foto no detalhe).

Regras: dono vê bytes originais (content-type real); 404 sem imagem;
403 cross-account; 401 sem Bearer.
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


def _jpeg(color=(9, 9, 9)):
    img = Image.new("RGB", (800, 600), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _upload(client, h, cid, seed=0):
    from app.schemas.extraction import ExtractionResult

    ok = ExtractionResult(is_homework=True, subject="Mat", title="T", statement="S",
                          due_at="2026-09-25", estimated_minutes=30, priority=1,
                          confidence=0.9, needs_review=False, extraction_status="ok", meta={})
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok):
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", _jpeg((seed, seed, seed)), "image/jpeg")},
                        data={"child_id": cid}, headers=h)
    assert r.status_code == 202, r.text
    return r.json()["homework_id"]


def test_owner_gets_original_bytes():
    from app.main import app

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    hid = _upload(client, h, cid)
    r = client.get(f"/v1/homeworks/{hid}/image", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:3] == b"\xff\xd8\xff"


def test_image_403_401_404():
    from app.main import app

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    hid = _upload(client, h, cid)
    assert client.get(f"/v1/homeworks/{hid}/image").status_code == 401
    h2, _ = make_auth(client, name="Outro")
    r = client.get(f"/v1/homeworks/{hid}/image", headers=h2)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    r = client.get(f"/v1/homeworks/{uuid.uuid4()}/image", headers=h)
    assert r.status_code == 404
