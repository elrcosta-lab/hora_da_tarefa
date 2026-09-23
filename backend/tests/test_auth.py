"""TDD RED — Auth JWT + escopo por dono (SPECS §10.5, RNF-06, CA-05).

Regras:
- register {name,email,password} → 201; e-mail duplicado → 409
- login → access (15min) + refresh (7 dias); credencial errada → 401
- refresh gira novo access; refresh inválido → 401
- sem Bearer → 401; conta A não acessa criança/tarefa da conta B → 403 FORBIDDEN
- senha jamais volta na API; hash Argon2id
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-min-0123456789")
    from app.tasks import routine as R
    from app.tasks import users as U
    from app.tasks.extract import clear_store

    R.clear_routine()
    U.clear_users()
    clear_store()
    yield
    R.clear_routine()
    U.clear_users()
    clear_store()


def _client():
    from app.main import app

    return TestClient(app)


def _register(c, name="Carlos", email="carlos@teste.com", password="senha-forte-123"):
    r = c.post("/v1/auth/register", json={"name": name, "email": email, "password": password,
                                          "lgpd_consent": True, "lgpd_version": "termos-v1"})
    assert r.status_code == 201, r.text
    return r.json()


def _approve(user_id: str):
    """RF-16: testes de auth focam no fluxo autenticado; aprovação explícita."""
    from app.tasks import admin as A

    return A.approve_user(user_id)


def _login(c, email="carlos@teste.com", password="senha-forte-123"):
    r = c.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_register_login_refresh_flow():
    c = _client()
    reg = _register(c)
    assert "user_id" in reg
    assert reg["status"] == "pending"
    assert "password" not in str(reg).lower() and "hash" not in str(reg).lower()
    _approve(reg["user_id"])
    tok = _login(c)
    assert tok["token_type"] == "bearer"
    assert "access_token" in tok and "refresh_token" in tok
    r = c.post("/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()


def test_register_duplicate_email_409_and_login_wrong_401():
    c = _client()
    _register(c)
    r = c.post("/v1/auth/register", json={"name": "X", "email": "carlos@teste.com", "password": "outra-senha-123",
                                          "lgpd_consent": True})
    assert r.status_code == 409
    r = c.post("/v1/auth/login", json={"email": "carlos@teste.com", "password": "errada-errada-errada"})
    assert r.status_code == 401
    r = c.post("/v1/auth/refresh", json={"refresh_token": "invalido"})
    assert r.status_code == 401


def test_protected_routes_require_bearer():
    c = _client()
    assert c.get("/v1/children").status_code == 401
    assert c.get("/v1/homeworks").status_code == 401
    assert c.get("/v1/notifications").status_code == 401


def test_register_requires_lgpd_consent():
    c = _client()
    r = c.post("/v1/auth/register", json={"name": "N", "email": "n@teste.com", "password": "senha-forte-123"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CONSENT_REQUIRED"
    r = c.post("/v1/auth/register", json={"name": "N", "email": "n@teste.com", "password": "senha-forte-123",
                                          "lgpd_consent": True, "lgpd_version": "termos-v1"})
    assert r.status_code == 201, r.text
    from app.tasks import users as U

    stored = U.get_user(r.json()["user_id"])
    assert stored["lgpd_consent_at"] is not None


def test_refresh_rotation_and_reuse_revokes_family():
    c = _client()
    reg = _register(c)
    _approve(reg["user_id"])
    t1 = _login(c)
    r1 = c.post("/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r1.status_code == 200, r1.text
    t2 = r1.json()
    assert t2["refresh_token"] != t1["refresh_token"]
    # reuso do antigo → 401 e família revogada (o novo também morre)
    r = c.post("/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 401
    r = c.post("/v1/auth/refresh", json={"refresh_token": t2["refresh_token"]})
    assert r.status_code == 401


def test_cross_account_child_is_forbidden():
    c = _client()
    ra = _register(c, name="A", email="a@teste.com")
    _approve(ra["user_id"])
    ta = _login(c, email="a@teste.com")
    ha = {"Authorization": f"Bearer {ta['access_token']}"}
    rb = _register(c, name="B", email="b@teste.com")
    _approve(rb["user_id"])
    tb = _login(c, email="b@teste.com")
    hb = {"Authorization": f"Bearer {tb['access_token']}"}

    cid = c.post("/v1/children", json={"name": "Ana"}, headers=ha).json()["id"]
    # B lista só os seus
    assert all(x["id"] != cid for x in c.get("/v1/children", headers=hb).json()["items"])
    # B acessando agenda da criança de A → 403
    r = c.get(f"/v1/children/{cid}/agenda", headers=hb)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
