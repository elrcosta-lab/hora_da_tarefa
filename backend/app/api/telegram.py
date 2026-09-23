"""Webhook do Telegram (SPECS §3.10 + §6 v1.1)."""
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import JSONResponse

from app.bot.handlers import drain_outbox, handle_update
from app.core.config import get_settings
from app.core.ratelimit import limit

router = APIRouter(prefix="/v1/telegram", tags=["telegram"])


def flush_outbox_to_telegram(token: str | None = None) -> int:
    """Entrega o outbox via sendMessage (só com envio live). Retorna enviadas."""
    from app.bot import telegram_api as _tg

    settings = get_settings()
    token = token or settings.TELEGRAM_BOT_TOKEN
    live = settings.TELEGRAM_LIVE_SEND or settings.TELEGRAM_POLLING
    if not live or token in ("change-me", "test-token", ""):
        return 0
    sent = 0
    for chat_id, text in drain_outbox():
        try:
            _tg.send_message(token, chat_id, text)
            sent += 1
        except _tg.TelegramError:
            continue
    return sent


@router.post("/webhook", status_code=200,
               dependencies=[Depends(limit(120, 60, key="ip", prefix="tg"))])
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    settings = get_settings()
    import hmac as _hmac

    if not _hmac.compare_digest(x_telegram_bot_api_secret_token or "",
                                settings.TELEGRAM_WEBHOOK_SECRET or ""):
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Assinatura inválida.", "details": {}}},
        )
    try:
        update = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"ok": True})
    result = handle_update(update if isinstance(update, dict) else {})
    hid = result.get("homework_id")
    if hid and not result.get("deduplicated"):
        from app.tasks.extract import run_extraction

        background.add_task(run_extraction, hid)
    if settings.TELEGRAM_LIVE_SEND:
        background.add_task(flush_outbox_to_telegram)
    return JSONResponse(status_code=200, content=result)
