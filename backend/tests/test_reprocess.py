"""TDD RED — Reprocess + beat retry de extrações travadas (429 free-tier).

Regras:
- POST /homeworks/{id}/reprocess → 202 (dono; 403/404); reexecuta extração
- beat retenta processando/falhou com created_at > 5min, sem derrubar o tick
"""
import io
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_auth

TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _clean():
    from app.tasks.extract import clear_store

    clear_store()
    yield
    clear_store()


def _jpeg():
    img = Image.new("RGB", (800, 600), (11, 12, 13))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _ok_result():
    from app.schemas.extraction import ExtractionResult

    return ExtractionResult(is_homework=True, subject="Matemática", title="T", statement="S",
                            due_at="2026-09-25", estimated_minutes=30, priority=1,
                            confidence=0.9, needs_review=False, extraction_status="ok", meta={})


def test_reprocess_endpoint_reruns_extraction():
    from app.main import app
    from app.services.vision_openrouter import OpenRouterRateLimited

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    with patch("app.services.vision_openrouter.extract_homework",
               side_effect=OpenRouterRateLimited("429")):
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", _jpeg(), "image/jpeg")},
                        data={"child_id": cid}, headers=h)
    hid = r.json()["homework_id"]
    from app.tasks.extract import get_homework

    assert get_homework(hid)["extraction_status"] == "processando"

    with patch("app.services.vision_openrouter.extract_homework", return_value=_ok_result()):
        r = client.post(f"/v1/homeworks/{hid}/reprocess", headers=h)
        assert r.status_code == 202, r.text
    # background do TestClient executou com o mock de sucesso
    assert get_homework(hid)["extraction_status"] == "ok"

    h2, _ = make_auth(client, name="Outro")
    assert client.post(f"/v1/homeworks/{hid}/reprocess", headers=h2).status_code == 403


def test_beat_retries_stale_processing():
    from app.main import app
    from app.services.vision_openrouter import OpenRouterRateLimited
    from app.tasks import beat as B

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    with patch("app.services.vision_openrouter.extract_homework",
               side_effect=OpenRouterRateLimited("429")):
        hid = client.post("/v1/homeworks/upload",
                          files={"file": (f"{uuid.uuid4()}.jpg", _jpeg(), "image/jpeg")},
                          data={"child_id": cid}, headers=h).json()["homework_id"]
    # envelhece o registro para além da janela de retry
    from app.core.db import session_scope
    from app.models import Homework

    with session_scope() as s:
        hw = s.get(Homework, hid)
        hw.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    with patch("app.services.vision_openrouter.extract_homework", return_value=_ok_result()):
        out = B.run_beat_tick(now=datetime.now(TZ))
    assert out["retried"] >= 1
    from app.tasks.extract import get_homework

    assert get_homework(hid)["extraction_status"] == "ok"
