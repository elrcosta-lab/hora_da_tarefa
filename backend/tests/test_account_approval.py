"""TDD RED — RF-16: conta nova só usa a plataforma após aprovação do admin.

Regras (SPECS v1.3 §10.5):
- register → 201 com status=pending, sem tokens
- login pendente → 403 ACCOUNT_PENDING; rejeitada → 403 ACCOUNT_REJECTED
- admin aprova (POST /v1/admin/users/:id/approve) → login 200
- admin rejeita (POST .../reject) → 403 + sessões revogadas; admin não pode ser rejeitado (409)
- só admin aprova/rejeita (403 comum, 401 sem token); aprovar inexistente → 404
- conta não-aprovada não gera link_code nem vincula no chat; gate do bot responde "em análise"
- seed admin nasce approved; contas existentes recebem backfill approved (migração 0012)
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-min-0123456789")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    from app.tasks import users as U

    U.clear_users()
    yield
    U.clear_users()


def _client():
    from app.main import app

    return TestClient(app)


def _register(c, name="Novo", email="novo@teste.com", password="senha-forte-123"):
    r = c.post("/v1/auth/register", json={"name": name, "email": email, "password": password,
                                          "lgpd_consent": True, "lgpd_version": "termos-v1"})
    assert r.status_code == 201, r.text
    return r.json()


def _admin_headers(c, email="admin-aprova@teste.com"):
    from app.tasks import admin as A

    admin = A.ensure_admin(email=email, password="Admin-forte-123")
    tok = c.post("/v1/auth/login", json={"email": email, "password": "Admin-forte-123"}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}, admin["user_id"]


def test_register_creates_pending_without_tokens():
    c = _client()
    body = _register(c)
    assert body["status"] == "pending"
    assert "access_token" not in body and "refresh_token" not in body


def test_login_pending_blocked():
    c = _client()
    _register(c)
    r = c.post("/v1/auth/login", json={"email": "novo@teste.com", "password": "senha-forte-123"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ACCOUNT_PENDING"


def test_admin_approves_then_login_works():
    c = _client()
    novo = _register(c)
    ha, _ = _admin_headers(c)
    r = c.post(f"/v1/admin/users/{novo['user_id']}/approve", headers=ha)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    t = c.post("/v1/auth/login", json={"email": "novo@teste.com",
                                       "password": "senha-forte-123"})
    assert t.status_code == 200
    assert "access_token" in t.json()


def test_admin_rejects_blocks_and_revokes():
    c = _client()
    novo = _register(c)
    ha, admin_id = _admin_headers(c)
    # aprova, loga (cria sessão), depois rejeita
    assert c.post(f"/v1/admin/users/{novo['user_id']}/approve", headers=ha).status_code == 200
    tok = c.post("/v1/auth/login", json={"email": "novo@teste.com",
                                         "password": "senha-forte-123"}).json()
    assert tok["refresh_token"]
    r = c.post(f"/v1/admin/users/{novo['user_id']}/reject", headers=ha)
    assert r.status_code == 200, r.text
    r2 = c.post("/v1/auth/login", json={"email": "novo@teste.com",
                                        "password": "senha-forte-123"})
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == "ACCOUNT_REJECTED"
    # refresh da sessão anterior morreu junto
    rr = c.post("/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert rr.status_code == 401
    _ = admin_id


def test_non_admin_cannot_approve_and_approve_404():
    from app.tasks import admin as A

    c = _client()
    novo = _register(c, name="Outro", email="outro@teste.com")
    ha, _ = _admin_headers(c)
    A.approve_user(novo["user_id"])
    t = c.post("/v1/auth/login", json={"email": "outro@teste.com",
                                       "password": "senha-forte-123"}).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert c.post(f"/v1/admin/users/{novo['user_id']}/approve", headers=h).status_code == 403
    assert c.post("/v1/admin/users/id-inexistente/aprove", headers=ha).status_code == 404
    assert c.post("/v1/admin/users/id-inexistente/reject", headers=ha).status_code == 404


def test_cannot_reject_admin():
    c = _client()
    ha, admin_id = _admin_headers(c)
    r = c.post(f"/v1/admin/users/{admin_id}/reject", headers=ha)
    assert r.status_code == 409


def test_seed_admin_is_approved():
    from app.tasks import admin as A

    a = A.ensure_admin(email="seed@teste.com", password="Seed-forte-123")
    assert a["status"] == "approved"


def test_pending_cannot_generate_link_code():
    from app.tasks import users as U

    c = _client()
    novo = _register(c)
    with pytest.raises(U.NotApproved):
        U.generate_link_code(novo["user_id"])


def test_bot_gate_blocks_pending_user():
    from app.bot import handlers as H
    from app.tasks import users as U

    c = _client()
    novo = _register(c)
    linked = U.link_telegram(U.get_user(novo["user_id"])["link_code"], 77)
    assert linked is not None
    r = c.post("/v1/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
               json={"update_id": 90,
                     "message": {"message_id": 1, "from": {"id": 77}, "chat": {"id": 77},
                                 "text": "/hoje"}})
    assert r.status_code == 200
    sent = H.last_sent(77) or ""
    assert "análise" in sent.lower()
    assert "Agenda de hoje" not in sent
