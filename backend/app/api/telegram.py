"""Webhook do Telegram (SPECS §3.10 + §6 v1.1)."""
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.bot.handlers import handle_update
from app.core.config import get_settings

router = APIRouter(prefix="/v1/telegram", tags=["telegram"])


@router.post("/webhook", status_code=200)
async def telegram_webhook(
    request: Request,
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
    return JSONResponse(status_code=200, content=result)
