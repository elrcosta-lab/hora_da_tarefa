"""F0: banco sqlite isolado p/ testes (prod usa Postgres via DATABASE_URL).

DATABASE_URL é fixada ANTES de qualquer import de app.* para o engine
pegar o sqlite de teste. Cada teste recebe tabelas limpas (reset_db).
"""
import os

_TEST_DB = "/tmp/opencode/hora-tarefa-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

import pytest  # noqa: E402

from app.core.db import init_db, reset_db  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    yield
    reset_db()
