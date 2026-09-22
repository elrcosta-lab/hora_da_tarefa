"""Histórico + settings de notificações (SPECS §3.11, RF-10)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.tasks import notify as N

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@router.get("", status_code=200)
def list_notifications_view(homework_id: str | None = None, child_id: str | None = None):
    items = N.list_notifications(homework_id=homework_id, child_id=child_id)
    return {
        "items": [
            {
                "id": r["id"],
                "homework_id": r["homework_id"],
                "child_id": r["child_id"],
                "kind": r["kind"],
                "scheduled_for": r["scheduled_for"].isoformat(),
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                "status": r["status"],
                "idempotency_key": r["idempotency_key"],
                "attempts": r["attempts"],
            }
            for r in items
        ],
        "total": len(items),
    }


class SettingsIn(BaseModel):
    child_id: str
    lembrete_24h: bool | None = None
    lembrete_2h: bool | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None


@router.post("/settings", status_code=200)
def update_settings_view(payload: SettingsIn):
    patch = {k: v for k, v in payload.model_dump().items() if k != "child_id" and v is not None}
    cur = N.update_settings(payload.child_id, patch)
    return {"child_id": payload.child_id, **cur}
