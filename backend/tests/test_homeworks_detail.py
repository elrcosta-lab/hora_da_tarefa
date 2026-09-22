"""TDD RED — upload→background + GET list/detail (SPECS §3.3 §3.4 §5 v1.1)."""
import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from unittest.mock import MagicMock, patch


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
        meta={"engine": "google/gemma-4-26b-a4b-it:free", "provider": "openrouter"},
    )


def test_upload_triggers_background_extraction():
    from app.main import app

    client = TestClient(app)
    child_id = str(uuid.uuid4())
    with patch(
        "app.services.vision_openrouter.extract_homework", return_value=_mock_success()
    ):
        resp = client.post(
            "/v1/homeworks/upload",
            files={"file": ("tarefa.jpg", _img_bytes(), "image/jpeg")},
            data={"child_id": child_id},
        )
        assert resp.status_code == 202
        hid = resp.json()["homework_id"]

        detail = client.get(f"/v1/homeworks/{hid}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["subject"] == "Matemática"
        assert body["extraction_status"] == "ok"
        assert body["extraction_confidence"] == pytest.approx(0.91)


def test_get_homework_detail_404():
    from app.main import app

    client = TestClient(app)
    resp = client.get(f"/v1/homeworks/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "HOMEWORK_NOT_FOUND"


def test_list_homeworks_filters_by_child():
    from app.main import app

    client = TestClient(app)
    child_a = str(uuid.uuid4())
    child_b = str(uuid.uuid4())
    with patch(
        "app.services.vision_openrouter.extract_homework", return_value=_mock_success()
    ):
        for i in range(2):
            client.post(
                "/v1/homeworks/upload",
                files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(size=(800 + i, 600), color=(200 + i, 220, 255)), "image/jpeg")},
                data={"child_id": child_a},
            )
        client.post(
            "/v1/homeworks/upload",
            files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(size=(900, 600), color=(10, 20, 30)), "image/jpeg")},
            data={"child_id": child_b},
        )
    resp = client.get("/v1/homeworks", params={"child_id": child_a})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(i["child_id"] == child_a for i in body["items"])
