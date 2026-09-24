"""TDD RED — Bot restrito a telegram_user_id vinculados (LGPD/segurança).

Regras:
- sem vínculo: qualquer comando/foto recebe mensagem de acesso restrito e NADA é criado
- pareamento: POST /v1/auth/telegram/link gera código de 6 dígitos; enviar o
  código (ou /start <código>) no bot vincula o chat e libera o acesso
- código é single-use (regenerar após uso)
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_auth


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    from app.tasks import routine as R
    from app.tasks import users as U
    from app.tasks.extract import clear_store
    from app.bot import handlers as H

    R.clear_routine()
    U.clear_users()
    clear_store()
    H.clear_bot()
    yield
    R.clear_routine()
    U.clear_users()
    clear_store()
    H.clear_bot()


def _client():
    from app.main import app

    return TestClient(app)


def _headers():
    return {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}


def _upd(uid, **msg):
    base = {"message_id": 1, "from": {"id": 501}, "chat": {"id": 501}}
    base.update(msg)
    return {"update_id": uid, "message": base}


def _link_code_for(client, name="Carlos"):
    h, uid = make_auth(client, name=name)
    r = client.post("/v1/auth/telegram/link", json={"user_id": uid}, headers=h)
    assert r.status_code == 200, r.text
    return h, r.json()


def test_unlinked_user_gets_restricted_and_creates_nothing():
    c = _client()
    from app.bot.handlers import last_sent
    from app.tasks.extract import list_homeworks

    r = c.post("/v1/telegram/webhook", headers=_headers(), json=_upd(
        100, text="/hoje", **{"from": {"id": 502}, "chat": {"id": 502}}))
    assert r.status_code == 200
    assert "restrito" in (last_sent(502) or "").lower()

    r = c.post("/v1/telegram/webhook", headers=_headers(), json=_upd(
        101, photo=[{"file_id": "x"}], **{"from": {"id": 502}, "chat": {"id": 502}}))
    assert r.status_code == 200
    assert list_homeworks() == []


def test_link_code_flow_grants_access():
    c = _client()
    from app.bot.handlers import last_sent

    _, data = _link_code_for(c)
    code = data["link_code"]
    # código direto no chat vincula
    r = c.post("/v1/telegram/webhook", headers=_headers(), json=_upd(110, text=code))
    assert r.status_code == 200
    assert "vinculad" in (last_sent(501) or "").lower()
    # agora /hoje funciona (não é restrito)
    r = c.post("/v1/telegram/webhook", headers=_headers(), json=_upd(111, text="/hoje"))
    assert r.status_code == 200
    assert "restrito" not in (last_sent(501) or "").lower()


def test_start_with_code_links():
    c = _client()
    from app.bot.handlers import last_sent

    _, data = _link_code_for(c)
    r = c.post("/v1/telegram/webhook", headers=_headers(), json=_upd(
        120, text=f"/start {data['link_code']}"))
    assert r.status_code == 200
    assert "vinculad" in (last_sent(501) or "").lower()


def test_code_is_single_use():
    c = _client()
    from app.bot.handlers import last_sent

    _, data = _link_code_for(c)
    code = data["link_code"]
    c.post("/v1/telegram/webhook", headers=_headers(), json=_upd(130, text=code))
    # segundo chat tentando o mesmo código não vincula
    r = c.post("/v1/telegram/webhook", headers=_headers(), json=_upd(
        131, text=code, **{"from": {"id": 503}, "chat": {"id": 503}}))
    assert r.status_code == 200
    assert "vinculad" not in (last_sent(503) or "").lower()


def test_link_status_endpoint_reflects_link():
    c = _client()
    h, data = _link_code_for(c)
    r = c.get("/v1/auth/telegram/status", headers=h)
    assert r.status_code == 200
    assert r.json() == {"linked": False, "telegram_user_id": None}
    from app.tasks import users as U

    assert U.link_telegram(data["link_code"], 777) is not None
    r = c.get("/v1/auth/telegram/status", headers=h)
    assert r.json() == {"linked": True, "telegram_user_id": 777}


def test_link_endpoint_regenerates_code():
    c = _client()

    h_owner, first = _link_code_for(c)
    r = c.post("/v1/auth/telegram/link", json={"user_id": first["user_id"]}, headers=h_owner)
    assert r.status_code == 200
    assert r.json()["link_code"] != first["link_code"]


def test_expired_code_rejected():
    from datetime import datetime, timedelta, timezone

    from app.core.db import session_scope
    from app.models import AppUser
    from app.tasks import users as U

    c = _client()
    h_owner, first = _link_code_for(c)
    with session_scope() as s:
        u = s.get(AppUser, first["user_id"])
        u.link_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert U.link_telegram(first["link_code"], 778) is None
    # código fresco vincula normalmente
    code = c.post("/v1/auth/telegram/link", json={"user_id": first["user_id"]},
                  headers=h_owner).json()["link_code"]
    assert U.link_telegram(code, 778) is not None


def test_callback_concluir_other_users_task_is_rejected():
    """A2: callback `concluir:` forjado com UUID de outra família não pode
    concluir (lookup sem escopo + fallback fabricado)."""
    from app.bot.handlers import last_sent
    from app.tasks import users as U
    from app.tasks.extract import get_homework, get_or_create_homework

    c = _client()
    # vítima: conta aprovada + criança + tarefa ativa
    hv, uid_v = make_auth(c, name="Vitima")
    cid_v = c.post("/v1/children", json={"name": "Ana"}, headers=hv).json()["id"]
    rec, _ = get_or_create_homework(b"bytes-vitima-a2", child_id=cid_v,
                                    created_by_user_id=uid_v)
    hid_v = rec["homework_id"]
    # atacante: conta aprovada vinculada ao chat 502
    ha, uid_a = make_auth(c, name="Atacante")
    code_a = c.post("/v1/auth/telegram/link", json={"user_id": uid_a},
                    headers=ha).json()["link_code"]
    assert U.link_telegram(code_a, 502) is not None
    # callback forjado com o UUID COMPLETO da vítima
    upd = {"update_id": 140,
           "callback_query": {"id": "cb-forjado", "from": {"id": 502},
                              "message": {"message_id": 9, "chat": {"id": 502}},
                              "data": f"concluir:{hid_v}"}}
    r = c.post("/v1/telegram/webhook", headers=_headers(), json=upd)
    assert r.status_code == 200
    assert get_homework(hid_v)["status"] != "concluida"
    assert "concluída" not in (last_sent(502) or "").lower()
