"""Polling do Telegram (sem URL pública): getUpdates em thread dedicada.

Ativo com TELEGRAM_POLLING=true. Reusa handle_update (com dedupe por update_id)
e descarrega o outbox via sendMessage. Erros de rede viram backoff curto;
409 (webhook ativo em outro lugar) desliga o loop com log.
"""
import logging
import threading
import time

log = logging.getLogger("hora_da_tarefa.bot")


def _run_extraction_async(homework_id: str) -> None:
    """Dispara a extração em thread daemon (bug 2026-09-25: o polling criava
    a tarefa mas nunca agendava a extração — só a rota webhook fazia isso).
    Erro aqui nunca derruba o loop (log + segue)."""
    from app.tasks.extract import run_extraction

    try:
        run_extraction(homework_id)
    except Exception:
        log.exception("polling: falha na extração %s", homework_id)


def run_polling(stop_event: threading.Event, token: str, max_iterations: int | None = None) -> dict:
    """Loop de long polling. Retorna estatísticas. max_iterations só p/ testes."""
    from app.api.telegram import flush_outbox_to_telegram
    from app.bot import telegram_api as _tg
    from app.bot.handlers import handle_update

    stats = {"updates": 0, "errors": 0}
    offset: int | None = None
    iterations = 0
    backoff = 1
    while not stop_event.is_set():
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1
        try:
            updates = _tg.get_updates(token, offset=offset, timeout=20)
            backoff = 1
        except _tg.TelegramError as exc:
            stats["errors"] += 1
            if "409" in str(exc) or "conflict" in str(exc).lower():
                # webhook (talvez em propagação de delete) — espera longo e tenta de novo,
                # nunca desliga sozinho: o modo é escolhido por TELEGRAM_POLLING
                log.warning("polling: conflito (webhook ativo?) — nova tentativa em 60s")
                stop_event.wait(60)
                continue
            log.warning("polling: %s (retry em %ss)", exc, backoff)
            stop_event.wait(backoff)
            backoff = min(backoff * 2, 30)
            continue
        for update in updates:
            try:
                uid = update.get("update_id")
                if uid is not None:
                    offset = uid + 1
                result = handle_update(update if isinstance(update, dict) else {})
                stats["updates"] += 1
                hid = (result or {}).get("homework_id")
                if hid and not (result or {}).get("deduplicated"):
                    threading.Thread(target=_run_extraction_async,
                                     args=(hid,), daemon=True).start()
            except Exception:
                log.exception("polling: falha ao processar update")
        try:
            flush_outbox_to_telegram(token)
        except Exception:
            log.exception("polling: falha no flush do outbox")
    return stats


def start_polling_thread(token: str) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    thread = threading.Thread(target=run_polling, args=(stop, token),
                              name="telegram-polling", daemon=True)
    thread.start()
    return stop, thread
