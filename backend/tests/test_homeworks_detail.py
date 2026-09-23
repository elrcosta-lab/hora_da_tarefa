"""upload→background + GET list/detail (SPECS §3.3 §3.4 §5 v1.1) — com auth."""
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


def _img_bytes(fmt="JPEG", size=(800, 600), color=(200, 220, 255)):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _mock_success():
    from app.schemas.extraction import ExtractionResult

    return ExtractionResult(
        is_homework=True,
        subject="Matemática",
        title="Lista de frações",
        statement="Resolver os exercícios 1 a 10 da página 42.",
        due_at="2026-09-25",
        estimated_minutes=40,
        priority=2,
        confidence=0.91,
        needs_review=False,
        extraction_status="ok",
        meta={"engine": "nex-agi/nex-n2.5-mini:free", "provider": "openrouter"},
    )


def _setup(client):
    h, _ = make_auth(client)
    a = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    b = client.post("/v1/children", json={"name": "Beto"}, headers=h).json()["id"]
    return h, a, b


def test_upload_triggers_background_extraction():
    from app.main import app

    client = TestClient(app)
    h, child_a, _ = _setup(client)
    with patch(
        "app.services.vision_openrouter.extract_homework", return_value=_mock_success()
    ):
        resp = client.post(
            "/v1/homeworks/upload",
            files={"file": ("tarefa.jpg", _img_bytes(), "image/jpeg")},
            data={"child_id": child_a},
            headers=h,
        )
        assert resp.status_code == 202
        hid = resp.json()["homework_id"]

        detail = client.get(f"/v1/homeworks/{hid}", headers=h)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["subject"] == "Matemática"
        assert body["extraction_status"] == "ok"
        assert body["extraction_confidence"] == pytest.approx(0.91)


def test_get_homework_detail_404():
    from app.main import app

    client = TestClient(app)
    h, _, _ = _setup(client)
    resp = client.get(f"/v1/homeworks/{uuid.uuid4()}", headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "HOMEWORK_NOT_FOUND"


def test_list_homeworks_filters_by_child():
    from app.main import app

    client = TestClient(app)
    h, child_a, child_b = _setup(client)
    with patch(
        "app.services.vision_openrouter.extract_homework", return_value=_mock_success()
    ):
        for i in range(2):
            client.post(
                "/v1/homeworks/upload",
                files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(size=(800 + i, 600), color=(200 + i, 220, 255)), "image/jpeg")},
                data={"child_id": child_a},
                headers=h,
            )
        client.post(
            "/v1/homeworks/upload",
            files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(size=(900, 600), color=(10, 20, 30)), "image/jpeg")},
            data={"child_id": child_b},
            headers=h,
        )
    resp = client.get("/v1/homeworks", params={"child_id": child_a}, headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(i["child_id"] == child_a for i in body["items"])
