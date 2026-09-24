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

    # A2: reprocess com teto igual ao upload (5/min) — sem loop drenando IA paga
    for _ in range(5):
        assert client.post(f"/v1/homeworks/{hid}/reprocess", headers=h).status_code == 202
    r6 = client.post(f"/v1/homeworks/{hid}/reprocess", headers=h)
    assert r6.status_code == 429
    assert r6.json()["error"]["code"] == "RATE_LIMITED"

    from app.core.ratelimit import get_limiter

    get_limiter().reset()  # isola o restante do teste do teto acima
    with patch("app.services.vision_openrouter.extract_homework", return_value=_ok_result()):
        r = client.post(f"/v1/homeworks/{hid}/reprocess", headers=h)
        assert r.status_code == 202, r.text
    # background do TestClient executou com o mock de sucesso
    assert get_homework(hid)["extraction_status"] == "ok"

    h2, _ = make_auth(client, name="Outro")
    assert client.post(f"/v1/homeworks/{hid}/reprocess", headers=h2).status_code == 403


def test_beat_stops_after_max_auto_attempts():
    from app.services.vision_openrouter import OpenRouterRateLimited
    from app.tasks import beat as B
    from app.tasks.extract import MAX_AUTO_ATTEMPTS, get_homework

    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    hid = _upload_stuck(client, h, cid)
    # esgota as tentativas automáticas
    for _ in range(MAX_AUTO_ATTEMPTS + 2):
        with patch("app.services.vision_openrouter.extract_homework",
                   side_effect=OpenRouterRateLimited("429")):
            B.run_beat_tick(now=datetime.now(TZ))
    rec = get_homework(hid)
    from app.core.db import session_scope
    from app.models import Homework

    with session_scope() as s:
        hw = s.get(Homework, hid)
        attempts = (hw.extraction_json or {}).get("meta", {}).get("attempts", 0)
    assert attempts <= MAX_AUTO_ATTEMPTS + 1  # inicial + beats até o teto
    # manual ainda funciona após o teto
    with patch("app.services.vision_openrouter.extract_homework", return_value=_ok_result()):
        r = client.post(f"/v1/homeworks/{hid}/reprocess", headers=h)
        assert r.status_code == 202


def _upload_stuck(client, h, cid):
    import io
    import uuid

    from PIL import Image

    from app.services.vision_openrouter import OpenRouterRateLimited

    img = Image.new("RGB", (800, 600), (5, 5, 5))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    with patch("app.services.vision_openrouter.extract_homework",
               side_effect=OpenRouterRateLimited("429")):
        return client.post("/v1/homeworks/upload",
                           files={"file": (f"{uuid.uuid4()}.jpg", buf.getvalue(), "image/jpeg")},
                           data={"child_id": cid}, headers=h).json()["homework_id"]


def _upload_failing(client, h, cid, error: str):
    """Upload cuja extração falha (mock) — para testar o caminho falhou."""
    from app.services.vision_openrouter import ExtractionFailed

    img = Image.new("RGB", (800, 600), (6, 6, 6))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    with patch("app.services.vision_openrouter.extract_homework",
               side_effect=ExtractionFailed(error)):
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", buf.getvalue(), "image/jpeg")},
                        data={"child_id": cid}, headers=h)
    assert r.status_code == 202, r.text
    return r.json()["homework_id"]


def test_failed_extraction_schedules_review_notice_once():
    """Bug real (2026-09-24): modelo pago devolveu 404 No-endpoints; o usuário viu
    'Recebi! Analisando...' e depois uma tarefa oca, sem nenhum aviso.
    Falha de extração agenda o aviso extracao_falhou (idempotente) e o dispatch
    o entrega uma única vez pedindo revisão manual.
    """
    from app.main import app
    from fastapi.testclient import TestClient

    from app.tasks import notify as N
    from app.tasks.extract import get_homework

    client = TestClient(app)
    h, uid = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    hid = _upload_failing(client, h, cid, "Error code: 404 - No endpoints found")
    assert get_homework(hid)["extraction_status"] == "falhou"
    kinds = [x["kind"] for x in N.list_notifications(homework_id=hid)]
    assert "extracao_falhou" in kinds
    # repetir a falha não duplica o aviso
    from app.tasks.extract import run_extraction
    from app.services.vision_openrouter import ExtractionFailed

    with patch("app.services.vision_openrouter.extract_homework",
               side_effect=ExtractionFailed("Error code: 404 - No endpoints found")):
        run_extraction(hid)
    assert sum(1 for x in N.list_notifications(homework_id=hid)
               if x["kind"] == "extracao_falhou") == 1
    # dispatch entrega pedindo revisão manual (dono com telegram vinculado)
    from app.tasks import users as U

    code = U.generate_link_code(uid)["link_code"]
    assert U.link_telegram(code, 555) is not None
    sent_log: list = []
    N.dispatch_due(now=datetime.now(TZ) + timedelta(days=1),
                   sender=lambda chat, text: sent_log.append(text))
    assert any("Revisar" in t for t in sent_log)


def test_beat_skips_no_endpoints_on_same_model():
    """404 No-endpoints no MESMO modelo = erro de config (nunca se autocura):
    o beat não queima tentativas nem chamadas nele. Se o modelo mudou,
    a retentativa continua permitida."""
    from app.main import app
    from fastapi.testclient import TestClient

    from app.tasks.extract import _should_skip_retry, get_homework
    from app.tasks.extract import retry_stale_extractions

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    hid = _upload_failing(client, h, cid, "Error code: 404 - No endpoints found")
    before = (get_homework(hid).get("extraction_json") or {}).get("meta", {}).get("attempts", 0)
    n = retry_stale_extractions(now=datetime.now(timezone.utc) + timedelta(minutes=30))
    assert n == 0
    after = (get_homework(hid).get("extraction_json") or {}).get("meta", {}).get("attempts", 0)
    assert after == before
    # unidade: mesmo modelo pula; modelo diferente ou outro erro não pula
    row = get_homework(hid)
    from app.core.config import get_settings

    assert _should_skip_retry(row, get_settings().OPENROUTER_MODEL) is True
    assert _should_skip_retry(row, "outro-modelo") is False
    row2 = dict(row, extraction_json={"meta": {"last_error": "500 boom"}})
    assert _should_skip_retry(row2, get_settings().OPENROUTER_MODEL) is False


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
