"""Histórico + settings de notificações (SPECS §3.11, RF-10) — escopo por dono."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.security import get_current_user_id
from app.tasks import notify as N
from app.tasks import routine as R
from app.tasks.extract import get_homework, owned_by

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


def _err(code: str, message: str, http: int) -> JSONResponse:
    return JSONResponse(status_code=http, content={"error": {"code": code, "message": message, "details": {}}})


@router.get("", status_code=200)
def list_notifications_view(homework_id: str | None = None, child_id: str | None = None,
                            owner: str = Depends(get_current_user_id)):
    if homework_id is not None:
        if get_homework(homework_id) is None:
            return _err("HOMEWORK_NOT_FOUND", "Tarefa não encontrada.", 404)
        if not owned_by(homework_id, owner):
            return _err("FORBIDDEN", "Sem acesso a esta tarefa.", 403)
    if child_id is not None:
        if R.get_child(child_id) is None:
            return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
        if not R.owns(child_id, owner):
            return _err("FORBIDDEN", "Sem acesso a esta criança.", 403)
    items = N.list_notifications(homework_id=homework_id, child_id=child_id, owner_user_id=owner)
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


@router.get("/settings", status_code=200)
def get_settings_view(child_id: str, owner: str = Depends(get_current_user_id)):
    if R.get_child(child_id) is None:
        return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
    if not R.owns(child_id, owner):
        return _err("FORBIDDEN", "Sem acesso a esta criança.", 403)
    return {"child_id": child_id, **N.get_settings(child_id)}


@router.post("/settings", status_code=200)
def update_settings_view(payload: SettingsIn, owner: str = Depends(get_current_user_id)):
    if R.get_child(payload.child_id) is None:
        return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
    if not R.owns(payload.child_id, owner):
        return _err("FORBIDDEN", "Sem acesso a esta criança.", 403)
    patch = {k: v for k, v in payload.model_dump().items() if k != "child_id" and v is not None}
    cur = N.update_settings(payload.child_id, patch)
    return {"child_id": payload.child_id, **cur}
