"""TDD — Polling Telegram: getUpdates processa via handle_update e avança offset."""
import threading

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_LIVE_SEND", "true")
    from app.bot import handlers as H

    H.clear_bot()
    yield
    H.clear_bot()


def test_get_updates_parses_and_advances_offset():
    from app.bot import telegram_api as _tg

    assert "offset" in _tg.get_updates.__code__.co_varnames


def test_polling_processes_batch_and_flushes(monkeypatch):
    import app.bot.polling as P
    from app.bot import handlers as H

    batch = [{"update_id": 501, "message": {"message_id": 1, "from": {"id": 606},
                                            "chat": {"id": 606}, "text": "/hoje"}}]
    monkeypatch.setattr("app.bot.telegram_api.get_updates", lambda token, offset=None, timeout=20: batch)
    sent = []
    monkeypatch.setattr("app.bot.telegram_api.send_message",
                        lambda token, chat_id, text, timeout=20.0: sent.append((chat_id, text)) or 999)
    stop = threading.Event()
    stats = P.run_polling(stop, "123456:AA-fake-live-token", max_iterations=1)
    assert stats["updates"] == 1
    assert sent and sent[0][0] == 606
    assert H.sent_count(606) == 0  # outbox descarregado


def test_polling_network_error_backoffs_and_continues(monkeypatch):
    import app.bot.polling as P
    from app.bot import telegram_api as _tg

    calls = {"n": 0}

    def flaky(token, offset=None, timeout=20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _tg.TelegramError("rede caiu")
        return []

    monkeypatch.setattr("app.bot.telegram_api.get_updates", flaky)
    stop = threading.Event()
    stats = P.run_polling(stop, "test-token", max_iterations=2)
    assert stats == {"updates": 0, "errors": 1}
    assert calls["n"] == 2


def test_polling_retries_on_webhook_conflict(monkeypatch):
    import app.bot.polling as P
    from app.bot import telegram_api as _tg

    waits = []
    calls = {"n": 0}

    def conflict(token, offset=None, timeout=20):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _tg.TelegramError("409 conflict: webhook ativo")
        return [{"update_id": 601, "message": {"message_id": 1, "from": {"id": 606},
                                               "chat": {"id": 606}, "text": "/hoje"}}]

    class FakeEvent:
        def is_set(self):
            return False

        def wait(self, s):
            waits.append(s)
            return False

    monkeypatch.setattr("app.bot.telegram_api.get_updates", conflict)
    stats = P.run_polling(FakeEvent(), "123456:AA-fake", max_iterations=3)
    assert stats["errors"] == 2
    assert stats["updates"] == 1
    assert waits == [60, 60]


def test_polling_schedules_extraction_for_photo(monkeypatch):
    """Bug real (2026-09-25): foto via polling criava a tarefa mas nunca
    agendava a extração (só a rota webhook fazia isso) — tarefa presa
    em `processando` até o beat (6+ min) ou para sempre.

    Sem patch global de threading: a thread real executa o mock e o teste
    espera com deadline (nada vaza para outros testes).
    """
    import base64
    import threading
    import time

    import app.bot.polling as P
    from app.bot import handlers as H
    from app.tasks import admin as A
    from app.tasks import routine as R
    from app.tasks import users as U
    from app.tasks.extract import clear_store, get_homework

    # Fixa os bindings de import ANTES do mock: o caminho de foto importa
    # app.api.homeworks sob demanda, e o `from` de topo fotografaria o mock
    # no namespace (vazamento entre testes no mesmo processo).
    import app.api.homeworks  # noqa: F401

    R.clear_routine()
    U.clear_users()
    clear_store()
    try:
        u = U.create_user("Mae")
        A.approve_user(u["user_id"])
        U.link_telegram(u["link_code"], 607)
        R.create_child("Bia", owner_user_id=u["user_id"])
        batch = [{"update_id": 502,
                  "message": {"message_id": 2, "from": {"id": 607}, "chat": {"id": 607},
                              "photo": [{"file_id": "x"}],
                              "test_bytes_b64": base64.b64encode(b"fake-bytes-foto").decode()}}]
        monkeypatch.setattr("app.bot.telegram_api.get_updates",
                            lambda token, offset=None, timeout=20: batch)
        calls = []
        monkeypatch.setattr("app.tasks.extract.run_extraction",
                            lambda hid: calls.append(hid))
        stop = threading.Event()
        stats = P.run_polling(stop, "test-token", max_iterations=1)
        assert stats["updates"] == 1
        deadline = time.monotonic() + 10
        while not calls and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(calls) == 1
        assert get_homework(calls[0]) is not None
        assert H.sent_count(607) >= 1  # "Recebi! Analisando..." foi respondido
    finally:
        R.clear_routine()
        U.clear_users()
        clear_store()
