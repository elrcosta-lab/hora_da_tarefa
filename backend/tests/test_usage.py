"""GET /usage soma tokens e estima custo (mecanismo de controle de gasto)."""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from unittest.mock import MagicMock, patch

from tests.conftest import make_auth


@pytest.fixture(autouse=True)
def _clean():
    from app.tasks.extract import clear_store

    clear_store()
    yield
    clear_store()


def test_usage_sums_tokens_and_estimates_cost():
    import json

    from app.main import app

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]

    fake_json = {"is_homework": True, "subject": "Mat", "title": "T", "statement": "S",
                 "due_at": "2026-09-25", "estimated_minutes": 30, "priority": 1,
                 "confidence": 0.9, "needs_review": False}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_json)))],
        usage=MagicMock(prompt_tokens=2000, completion_tokens=500),
    )
    with patch("app.services.vision_openrouter._get_client", return_value=mock_client):
        img = Image.new("RGB", (800, 600), (1, 2, 3))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", buf.getvalue(), "image/jpeg")},
                        data={"child_id": cid}, headers=h)
        assert r.status_code == 202

    r = client.get("/v1/usage", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extractions"] == 1
    assert body["prompt_tokens"] == 2000
    assert body["completion_tokens"] == 500
    # 2000/1M*0.025 + 500/1M*0.10 = 0.0001
    assert body["estimated_cost_usd"] == pytest.approx(0.0001)


def test_usage_requires_auth_and_is_scoped():
    from app.main import app

    client = TestClient(app)
    assert client.get("/v1/usage").status_code == 401
    h, _ = make_auth(client)
    assert client.get("/v1/usage", headers=h).json()["extractions"] == 0
    h2, _ = make_auth(client, name="Outro")
    assert client.get("/v1/usage", headers=h2).json()["prompt_tokens"] == 0
