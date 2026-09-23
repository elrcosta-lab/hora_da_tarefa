"""TDD RED — Filtros + Hoje + CSV (SPECS §3.3, RF-11/12).

Regras:
- GET /homeworks aceita status (CSV), subject, due_before/after, q, sort
- GET /homeworks/today agrega {due_today, overdue, scheduled_today} do dono
- GET /homeworks/export devolve CSV UTF-8 com os mesmos filtros
- tudo escopado por dono (sem vazar conta alheia)
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
    img = Image.new("RGB", (800 + seed, 600), (50 + seed, 60, 70))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mk_result(**over):
    from app.schemas.extraction import ExtractionResult

    base = dict(is_homework=True, subject="Matemática", title="Lista de frações",
                statement="Resolver ex 1 a 10", due_at="2026-09-25",
                estimated_minutes=30, priority=1, confidence=0.9,
                needs_review=False, extraction_status="ok", meta={})
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


def test_filters_combine_status_subject_q_period_sort():
    from app.main import app

    client = TestClient(app)
    h, cid = _setup(client)
    from app.tasks.extract import update_homework_fields

    # uploads com due futuro (extração rejeita passado); cenário fixo via edição manual
    h1 = _upload(client, h, cid, seed=1, subject="Matemática", title="Lista de frações", due_at="2099-01-01")
    h2 = _upload(client, h, cid, seed=2, subject="Português", title="Redação sustentabilidade", due_at="2099-01-01")
    update_homework_fields(h1, due_at="2026-09-25")
    update_homework_fields(h2, due_at="2026-09-20")
    h3 = _upload(client, h, cid, seed=3, subject="Matemática", title="Prova final", due_at="2026-09-28")
    client.patch(f"/v1/homeworks/{h3}/status", json={"status": "agendada"}, headers=h)
    client.patch(f"/v1/homeworks/{h3}/status", json={"status": "em_andamento"}, headers=h)
    client.patch(f"/v1/homeworks/{h3}/status", json={"status": "concluida"}, headers=h)

    r = client.get("/v1/homeworks", params={"subject": "Matemática"}, headers=h)
    assert r.json()["total"] == 2
    r = client.get("/v1/homeworks", params={"q": "frações"}, headers=h)
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == h1
    r = client.get("/v1/homeworks", params={"status": "concluida"}, headers=h)
    assert r.json()["total"] == 1
    r = client.get("/v1/homeworks", params={"status": "pendente,agendada"}, headers=h)
    assert r.json()["total"] == 2
    r = client.get("/v1/homeworks", params={"due_before": "2026-09-21"}, headers=h)
    assert r.json()["total"] == 1
    r = client.get("/v1/homeworks", params={"due_after": "2026-09-26"}, headers=h)
    assert r.json()["total"] == 1
    r = client.get("/v1/homeworks", params={"subject": "Matemática", "status": "concluida"}, headers=h)
    assert r.json()["total"] == 1
    r = client.get("/v1/homeworks", params={"sort": "-due_at"}, headers=h)
    dues = [i["due_at"] for i in r.json()["items"]]
    assert dues == sorted(dues, reverse=True)


def test_today_aggregates_due_overdue_scheduled():
    from app.main import app

    client = TestClient(app)
    h, cid = _setup(client)
    hid_today = _upload(client, h, cid, seed=11, due_at="2099-01-01", title="Para hoje")
    hid_late = _upload(client, h, cid, seed=12, due_at="2099-01-01", title="Atrasada")
    from app.tasks.extract import transition_homework, update_homework_fields

    update_homework_fields(hid_today, due_at="2026-09-22")
    update_homework_fields(hid_late, due_at="2020-01-01")  # vencida só por edição manual
    transition_homework(hid_late, "atrasada")
    r = client.get("/v1/homeworks/today", params={"date": "2026-09-22"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == "2026-09-22"
    assert any(i["id"] == hid_today for i in body["due_today"])
    assert any(i["id"] == hid_late for i in body["overdue"])
    assert isinstance(body["scheduled_today"], list)


def test_export_respects_max_rows():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.tasks.extract import export_csv

    c = TestClient(app)
    h, uid = make_auth(c)
    cid = c.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    _upload(c, h, cid, seed=41)
    _upload(c, h, cid, seed=42)
    text = export_csv(uid, child_id=cid, max_rows=1)
    assert len([ln for ln in text.strip().splitlines() if ln]) == 2  # header + 1


def test_export_csv_utf8_with_filters():
    from app.main import app

    client = TestClient(app)
    h, cid = _setup(client)
    _upload(client, h, cid, seed=21, subject="Matemática", title="Lista de frações")
    _upload(client, h, cid, seed=22, subject="Português", title="Redação")
    r = client.get("/v1/homeworks/export", params={"subject": "Matemática"}, headers=h)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    text = r.content.decode("utf-8-sig")
    lines = [ln for ln in text.strip().splitlines() if ln]
    assert lines[0].split(",")[0] == "id"
    assert len(lines) == 2
    assert "frações" in text


def test_filters_never_leak_other_account():
    from app.main import app

    client = TestClient(app)
    h, cid = _setup(client)
    _upload(client, h, cid, seed=31)
    h2, _ = make_auth(client, name="Outro")
    assert client.get("/v1/homeworks", headers=h2).json()["total"] == 0
    assert client.get("/v1/homeworks/today", headers=h2).json()["due_today"] == []
    assert len(client.get("/v1/homeworks/export", headers=h2).content.decode("utf-8-sig").strip().splitlines()) == 1
