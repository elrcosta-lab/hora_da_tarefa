"""Beat agendado (SPECS §1.2 scheduler, §6.4 retry, PRD RF-10).

Tick único: mark_overdue + dispatch_due. Em prod roda via APScheduler no
lifespan da API (1/min + purge 1x/dia); aqui a função é pura e testável.
"""
from datetime import datetime

from app.core.db import TZ


def send_telegram(chat_id: int, text: str) -> int | None:
    """Sender de produção: sendMessage com o token configurado."""
    from app.bot import telegram_api as _tg
    from app.core.config import get_settings

    settings = get_settings()
    if settings.TELEGRAM_BOT_TOKEN in ("change-me", "test-token", "", None):
        raise _tg.TelegramError("TELEGRAM_BOT_TOKEN não configurado")
    return _tg.send_message(settings.TELEGRAM_BOT_TOKEN, int(chat_id), text)


def _select_sender(settings):
    """Escolhe como o beat entrega: direto via Bot API (live), via outbox
    descarregado pelo polling, ou nada (marca sent, modo teste).

    O1: com TELEGRAM_LIVE_SEND=false o beat passava sender=None e os
    lembretes morriam em silêncio mesmo com polling ativo.
    """
    if settings.TELEGRAM_LIVE_SEND:
        return send_telegram
    if settings.TELEGRAM_POLLING:
        from app.bot.handlers import _send as _queue

        return lambda chat_id, text: _queue(int(chat_id), text)
    return None


def run_beat_tick(now: datetime | None = None, sender=None) -> dict:
    """Executa um ciclo: atrasos + envios + retry de extrações travadas."""
    from app.tasks import notify as _N
    from app.tasks.extract import mark_overdue, retry_stale_extractions

    now = now or datetime.now(TZ)
    overdue = mark_overdue(now.isoformat())
    stats: dict = {}
    _N.dispatch_due(now, sender=sender, stats=stats)
    retried = retry_stale_extractions(now, older_than_minutes=5)
    return {"overdue": overdue, "sent": stats.get("sent", 0),
            "errors": stats.get("errors", 0), "retried": retried}


def start_scheduler() -> object:
    """Sobe APScheduler em background (chamado no lifespan; BEAT_ENABLED=false desliga)."""
    import os

    from apscheduler.schedulers.background import BackgroundScheduler

    from app.tasks.extract import purge_expired_images

    sched = BackgroundScheduler(timezone=str(TZ))

    def _tick():
        from app.core.config import get_settings

        run_beat_tick(sender=_select_sender(get_settings()))

    sched.add_job(_tick, "interval", minutes=1, id="beat-tick", max_instances=1, coalesce=True)
    sched.add_job(purge_expired_images, "interval", hours=24, id="purge-images")
    if os.environ.get("BEAT_ENABLED", "true").lower() != "false":
        sched.start()
    return sched
