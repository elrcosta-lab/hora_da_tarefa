"""Webhook do Telegram (SPECS §3.10 + §6 v1.1)."""
from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from app.bot.handlers import drain_outbox, handle_update
from app.core.config import get_settings

router = APIRouter(prefix="/v1/telegram", tags=["telegram"])


def flush_outbox_to_telegram() -> int:
    """Entrega o outbox via sendMessage (só com TELEGRAM_LIVE_SEND). Retorna enviadas."""
    from app.bot import telegram_api as _tg

    settings = get_settings()
    if not settings.TELEGRAM_LIVE_SEND or settings.TELEGRAM_BOT_TOKEN in ("change-me", "test-token", ""):
        return 0
    sent = 0
    for chat_id, text in drain_outbox():
        try:
            _tg.send_message(settings.TELEGRAM_BOT_TOKEN, chat_id, text)
            sent += 1
        except _tg.TelegramError:
            continue
    return sent


@router.post("/webhook", status_code=200)
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    settings = get_settings()
    if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Assinatura inválida.", "details": {}}},
        )
    try:
        update = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"ok": True})
    result = handle_update(update if isinstance(update, dict) else {})
    if settings.TELEGRAM_LIVE_SEND:
        background.add_task(flush_outbox_to_telegram)
    return JSONResponse(status_code=200, content=result)
