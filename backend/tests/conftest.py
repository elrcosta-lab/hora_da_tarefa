"""F0: banco sqlite isolado p/ testes (prod usa Postgres via DATABASE_URL).

DATABASE_URL é fixada ANTES de qualquer import de app.* para o engine
pegar o sqlite de teste. Cada teste recebe tabelas limpas (reset_db).
"""
import os

_TEST_DB = "/tmp/opencode/hora-tarefa-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-min-0123456789")
# NUNCA a chave real: Settings lê .env do repo; testes usam dummy (falha rápida 401)
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test-dummy-key"
# A4: backdoor de bytes só nos testes
os.environ["ALLOW_TEST_BYTES"] = "true"
# storage local isolado por padrão (test_storage.py sobrescreve por teste)
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("STORAGE_LOCAL_DIR", "/tmp/opencode/hora-images-test")

import pytest  # noqa: E402

from app.core.db import init_db, reset_db  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    import shutil

    from app.core.ratelimit import get_limiter

    get_limiter().reset()
    init_db()
    yield
    reset_db()
    get_limiter().reset()
    shutil.rmtree(os.environ["STORAGE_LOCAL_DIR"], ignore_errors=True)


def make_auth(client, name="Teste", email=None, password="senha-forte-123"):
    """Registra + aprova (RF-16) + loga e devolve (headers, user_id)."""
    import uuid as _uuid

    email = email or f"{name.lower()}-{_uuid.uuid4().hex[:8]}@teste.com"
    r = client.post("/v1/auth/register", json={"name": name, "email": email, "password": password,
                                               "lgpd_consent": True, "lgpd_version": "termos-v1"})
    assert r.status_code == 201, r.text
    from app.tasks import admin as A

    A.approve_user(r.json()["user_id"])
    t = client.post("/v1/auth/login", json={"email": email, "password": password}).json()
    return {"Authorization": f"Bearer {t['access_token']}"}, t["user_id"]
