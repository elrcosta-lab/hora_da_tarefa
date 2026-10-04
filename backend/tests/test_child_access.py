"""TDD — RF-25: criança acessa por código; página somente-leitura (SPECS §2.14 §3.12 §10.5).

- pai dono gera código 8 chars Crockford retornado 1x; GET nunca devolve o código
- login válido → JWT type=child_access, sub=child_id; inválido/revogado → 401 genérico
- regenerar invalida o anterior; revogar invalida login e token já emitido
- /v1/child/homeworks filtra pelo sub do token; read-only; sem child_id de entrada
- token de criança em rota de pai → 401; token de pai em rota de criança → 401
- cross-account no access-code → 403; criança inexistente → 404
- burst 5/min por IP → 429 com Retry-After; lockout por falhas
- code_hash persistido (Argon2); plaintext ausente; sem owner exposto
"""
import logging
import uuid

import jwt as _jwt
import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_auth


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app)


def _kids(client, h, names=("Ana", "Beto")):
    ids = []
    for n in names:
        r = client.post("/v1/children", json={"name": n}, headers=h)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def _gen(client, h, cid):
    r = client.post(f"/v1/children/{cid}/access-code", headers=h)
    assert r.status_code == 201, r.text
    return r.json()["code"]


def _login(client, code):
    return client.post("/v1/auth/child/login", json={"code": code})


def _mk_homework(child_id, title, subject="Matemática"):
    from app.core.db import session_scope
    from app.models import Homework

    with session_scope() as s:
        s.add(Homework(id=str(uuid.uuid4()), child_id=child_id, subject=subject,
                       title=title, status="pendente"))


def test_generate_code_returns_once_and_never_again(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    code = _gen(client, h, cid)
    assert len(code) == 8
    assert set(code) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    for _ in range(2):
        g = client.get(f"/v1/children/{cid}/access-code", headers=h)
        assert g.status_code == 200, g.text
        assert "code" not in g.json()
        assert g.json()["active"] is True


def test_code_is_hashed_not_plaintext(client):
    from app.core.db import session_scope
    from app.core.security import verify_child_code
    from app.models import ChildAccess

    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    code = _gen(client, h, cid)
    with session_scope() as s:
        row = s.get(ChildAccess, cid)
        assert row is not None
        assert row.code_hash != code
        assert code not in row.code_hash
    assert verify_child_code(code, row.code_hash) is True
    assert verify_child_code("ZZZZZZZZ", row.code_hash) is False


def test_login_valid_code_issues_child_token(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    code = _gen(client, h, cid)
    r = _login(client, code)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 7200
    assert body["child"]["id"] == cid
    assert "refresh_token" not in body
    payload = _jwt.decode(body["access_token"], options={"verify_signature": False})
    assert payload["type"] == "child_access"
    assert payload["sub"] == cid


def test_login_invalid_code_is_generic_401(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    code = _gen(client, h, cid)
    t = _login(client, code).json()["access_token"]
    client.delete(f"/v1/children/{cid}/access-code", headers=h)
    bad = _login(client, "AAAAAAAA")
    gone = _login(client, code)
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CODE"
    assert gone.status_code == 401 and gone.json() == bad.json()
    # token emitido antes da revogação também cai
    me = client.get("/v1/child/me", headers={"Authorization": f"Bearer {t}"})
    assert me.status_code == 401


def test_code_normalization_accepts_ambiguous_chars(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    code = _gen(client, h, cid)
    variant = code.lower()[:4] + "-" + code.lower()[4:]
    r = _login(client, variant)
    assert r.status_code == 200, r.text


def test_regenerate_invalidates_old_code(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    old = _gen(client, h, cid)
    new = _gen(client, h, cid)
    assert new != old
    assert _login(client, old).status_code == 401
    assert _login(client, new).status_code == 200


def test_child_me_minimal_fields(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    t = _login(client, _gen(client, h, cid)).json()["access_token"]
    me = client.get("/v1/child/me", headers={"Authorization": f"Bearer {t}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["id"] == cid and body["name"] == "Ana"
    for forbidden in ("owner_user_id", "birth_date", "school_name", "code_hash"):
        assert forbidden not in body


def test_child_homeworks_only_own_child(client):
    h, _ = make_auth(client)
    a, b = _kids(client, h)
    _mk_homework(a, "Lista de frações")
    _mk_homework(b, "Redação do Beto", subject="Português")
    ta = _login(client, _gen(client, h, a)).json()["access_token"]
    r = client.get("/v1/child/homeworks?page_size=100", headers={"Authorization": f"Bearer {ta}"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["title"] == "Lista de frações"
    # ?child_id do irmão é ignorado (não declarado) — nada vaza
    r2 = client.get(f"/v1/child/homeworks?page_size=100&child_id={b}",
                    headers={"Authorization": f"Bearer {ta}"})
    assert r2.status_code == 200
    assert all(i["child_id"] == a for i in r2.json()["items"])


def test_child_token_rejected_on_parent_routes(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    t = _login(client, _gen(client, h, cid)).json()["access_token"]
    ch = {"Authorization": f"Bearer {t}"}
    assert client.get("/v1/children", headers=ch).status_code == 401
    assert client.get("/v1/homeworks", headers=ch).status_code == 401


def test_parent_token_rejected_on_child_routes(client):
    h, _ = make_auth(client)
    _kids(client, h, ("Ana",))
    assert client.get("/v1/child/me", headers=h).status_code == 401
    assert client.get("/v1/child/homeworks", headers=h).status_code == 401


def test_cross_account_cannot_manage_access_code(client):
    h1, _ = make_auth(client, name="Pai1")
    h2, _ = make_auth(client, name="Pai2")
    (cid,) = _kids(client, h1, ("Ana",))
    assert client.post(f"/v1/children/{cid}/access-code", headers=h2).status_code == 403
    assert client.get(f"/v1/children/{cid}/access-code", headers=h2).status_code == 403
    assert client.delete(f"/v1/children/{cid}/access-code", headers=h2).status_code == 403


def test_access_code_unknown_child_404(client):
    h, _ = make_auth(client)
    fake = str(uuid.uuid4())
    assert client.post(f"/v1/children/{fake}/access-code", headers=h).status_code == 404
    assert client.get(f"/v1/children/{fake}/access-code", headers=h).status_code == 404
    assert client.delete(f"/v1/children/{fake}/access-code", headers=h).status_code == 404
    (cid,) = _kids(client, h, ("Ana",))
    r = client.delete(f"/v1/children/{cid}/access-code", headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "CHILD_ACCESS_NOT_FOUND"


def test_child_login_burst_rate_limited(client):
    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    code = _gen(client, h, cid)
    statuses = [_login(client, code).status_code for _ in range(6)]
    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429
    last = _login(client, code)
    assert last.status_code == 429
    assert "Retry-After" in last.headers


def test_child_login_lockout_after_failures(client):
    from app.core.ratelimit import child_login_locked, note_child_login_failure

    ip = "10.9.9.9"
    assert child_login_locked(ip) is False
    for _ in range(11):
        note_child_login_failure(ip)
    assert child_login_locked(ip) is True
    assert child_login_locked("10.9.9.10") is False


def _view_login(code: str, ip: str):
    """Chama a view direto (sem o burst 5/min do roteamento) p/ testar lockout/reset."""
    from fastapi import Request

    from app.api.auth import ChildLoginIn, child_login_view

    scope = {"type": "http", "method": "POST", "headers": [], "client": (ip, 5000)}
    return child_login_view(ChildLoginIn(code=code), Request(scope))


def test_malformed_code_does_not_count_toward_lockout(client):
    """A7: formato inválido (400, sem Argon2) não alimenta o lockout."""
    from fastapi.responses import JSONResponse

    from app.core.ratelimit import child_login_locked

    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    good = _gen(client, h, cid)
    ip = "10.9.9.21"
    for _ in range(11):
        r = _view_login("***curto", ip)
        assert isinstance(r, JSONResponse) and r.status_code == 400
    assert child_login_locked(ip) is False
    assert isinstance(_view_login(good, ip), dict)


def test_success_resets_failure_counter(client):
    """A7: login válido zera as falhas — 9 erros + acerto + 9 erros não bloqueia."""
    from fastapi.responses import JSONResponse

    from app.core.ratelimit import child_login_locked

    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    good = _gen(client, h, cid)
    ip = "10.9.9.22"
    for _ in range(9):
        r = _view_login("AAAAAAAA", ip)
        assert isinstance(r, JSONResponse) and r.status_code == 401
    assert isinstance(_view_login(good, ip), dict)
    assert child_login_locked(ip) is False
    for _ in range(9):
        r = _view_login("AAAAAAAA", ip)
        assert isinstance(r, JSONResponse) and r.status_code == 401
    assert child_login_locked(ip) is False  # sem reset, 18 > 10 travaria


def test_login_does_not_log_code(client, caplog):
    h, _ = make_auth(client)
    _kids(client, h, ("Ana",))
    secret = "Q1W2E3R4"
    with caplog.at_level(logging.DEBUG):
        r = _login(client, secret)
    assert r.status_code == 401
    assert all(secret not in (rec.getMessage() or "") for rec in caplog.records)


def test_last_login_at_updated(client):
    from app.core.db import session_scope
    from app.models import ChildAccess

    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    assert _login(client, _gen(client, h, cid)).status_code == 200
    with session_scope() as s:
        assert s.get(ChildAccess, cid).last_login_at is not None


def test_code_lookup_is_hmac_not_plaintext(client):
    """A5: lookup O(1) indexado — HMAC determinístico, sem o código em claro."""
    from app.core.db import session_scope
    from app.core.security import child_code_lookup
    from app.models import ChildAccess

    h, _ = make_auth(client)
    a, b = _kids(client, h)
    ca, cb = _gen(client, h, a), _gen(client, h, b)
    assert _login(client, ca).status_code == 200
    assert _login(client, cb).status_code == 200
    with session_scope() as s:
        ra, rb = s.get(ChildAccess, a), s.get(ChildAccess, b)
        for row, code in ((ra, ca), (rb, cb)):
            assert len(row.code_lookup) == 64
            assert code not in row.code_lookup
            assert row.code_lookup == child_code_lookup(code)
        assert ra.code_lookup != rb.code_lookup
    assert child_code_lookup("***") is None


def test_legacy_row_without_lookup_still_logs_in(client):
    """A5: linha pré-0014 (code_lookup='') autentica pelo fallback restrito."""
    from datetime import datetime, timezone

    from app.core.db import session_scope
    from app.models import ChildAccess

    h, _ = make_auth(client)
    (cid,) = _kids(client, h, ("Ana",))
    code = _gen(client, h, cid)
    with session_scope() as s:
        row = s.get(ChildAccess, cid)
        row.code_lookup = ""
        row.updated_at = datetime.now(timezone.utc)
    assert _login(client, code).status_code == 200
