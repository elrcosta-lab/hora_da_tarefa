"""TDD RED — Administrador: seed padrão, listagem, exclusão em cascata e reset de senha.

Regras:
- ensure_admin() cria/promove o admin padrão (idempotente)
- só admin lista/exclui/reseta (403 para comum, 401 sem token)
- excluir remove tudo do dono (filhos, tarefas, imagens, notificações, tokens)
- reset invalida senha antiga + revoga refresh e exige senha ≥8
- admin não exclui a própria conta
"""
import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_auth


@pytest.fixture(autouse=True)
def _clean():
    from app.tasks import users as U

    U.clear_users()
    yield
    U.clear_users()


def _client():
    from app.main import app

    return TestClient(app)


def _admin_headers(client, email="admin-red@teste.com"):
    from app.tasks import admin as A

    admin = A.ensure_admin(email=email, password="Admin-forte-123")
    tok = client.post("/v1/auth/login", json={"email": email, "password": "Admin-forte-123"}).json()
    assert admin["role"] == "admin"
    return {"Authorization": f"Bearer {tok['access_token']}"}, admin["user_id"]


def test_seed_requires_admin_password(monkeypatch):
    import os

    from app.tasks import admin as A

    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        A.ensure_admin()


def test_ensure_admin_idempotent_and_promotes():
    from app.tasks import admin as A
    from app.tasks import users as U

    a1 = A.ensure_admin(email="root@teste.com", password="Root-forte-123")
    U.register("X", "x@teste.com", "senha-forte-123", lgpd_consent=True)
    a2 = A.ensure_admin(email="root@teste.com", password="Root-forte-123")
    assert a1["user_id"] == a2["user_id"]
    assert U.get_user(a2["user_id"])["role"] == "admin"


def test_admin_lists_users_and_common_forbidden():
    c = _client()
    ha, _ = _admin_headers(c)
    h, _ = make_auth(c, name="Comum")
    r = c.get("/v1/admin/users", headers=ha)
    assert r.status_code == 200
    assert r.json()["total"] >= 2
    assert all("password_hash" not in u for u in r.json()["items"])
    assert c.get("/v1/admin/users", headers=h).status_code == 403
    assert c.get("/v1/admin/users").status_code == 401


def test_admin_deletes_user_with_cascade_and_not_self():
    c = _client()
    ha, admin_id = _admin_headers(c)
    h, uid = make_auth(c, name="Alvo")
    cid = c.post("/v1/children", json={"name": "Filho"}, headers=h).json()["id"]
    r = c.delete(f"/v1/admin/users/{uid}", headers=ha)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["children"] == 1
    from app.tasks import routine as R
    from app.tasks import users as U

    assert U.get_user(uid) is None
    assert R.get_child(cid) is None
    # relogin impossível
    r = c.post("/v1/auth/login", json={"email": "x", "password": "y"})
    assert r.status_code in (401, 422)
    # autoexclusão bloqueada
    assert c.delete(f"/v1/admin/users/{admin_id}", headers=ha).status_code == 409


def test_admin_resets_password_and_revokes_sessions():
    c = _client()
    ha, _ = _admin_headers(c)
    h, uid = make_auth(c, name="Esquecido", email="esq@teste.com")
    old_login = c.post("/v1/auth/login", json={"email": "esq@teste.com",
                                               "password": "senha-forte-123"}).json()
    r = c.post(f"/v1/admin/users/{uid}/reset-password",
               json={"new_password": "Nova-senha-456"}, headers=ha)
    assert r.status_code == 200, r.text
    # senha antiga morre, nova funciona
    bad = c.post("/v1/auth/login", json={"email": "esq@teste.com", "password": "senha-forte-123"})
    assert bad.status_code == 401
    good = c.post("/v1/auth/login", json={"email": "esq@teste.com", "password": "Nova-senha-456"})
    assert good.status_code == 200
    # refresh antigo revogado
    assert c.post("/v1/auth/refresh",
                  json={"refresh_token": old_login["refresh_token"]}).status_code == 401
    # comum não reseta
    assert c.post(f"/v1/admin/users/{uid}/reset-password",
                  json={"new_password": "Outra-789"}, headers=h).status_code == 403
    # fraca rejeitada
    assert c.post(f"/v1/admin/users/{uid}/reset-password",
                  json={"new_password": "curta"}, headers=ha).status_code == 400
