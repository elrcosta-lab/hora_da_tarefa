"""F0: banco sqlite isolado p/ testes (prod usa Postgres via DATABASE_URL).

DATABASE_URL é fixada ANTES de qualquer import de app.* para o engine
pegar o sqlite de teste. Cada teste recebe tabelas limpas (reset_db).
"""
import os

_TEST_DB = "/tmp/opencode/hora-tarefa-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
# storage local isolado por padrão (test_storage.py sobrescreve por teste)
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("STORAGE_LOCAL_DIR", "/tmp/opencode/hora-images-test")

import pytest  # noqa: E402

from app.core.db import init_db, reset_db  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    import shutil

    init_db()
    yield
    reset_db()
    shutil.rmtree(os.environ["STORAGE_LOCAL_DIR"], ignore_errors=True)
