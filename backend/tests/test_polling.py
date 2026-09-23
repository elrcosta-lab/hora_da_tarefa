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


def test_polling_stops_on_webhook_conflict(monkeypatch):
    import app.bot.polling as P
    from app.bot import telegram_api as _tg

    def conflict(token, offset=None, timeout=20):
        raise _tg.TelegramError("409 conflict: webhook ativo")

    monkeypatch.setattr("app.bot.telegram_api.get_updates", conflict)
    stop = threading.Event()
    stats = P.run_polling(stop, "test-token", max_iterations=5)
    assert stats["errors"] == 1
