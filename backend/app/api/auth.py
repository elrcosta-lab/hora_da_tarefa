"""Pareamento Telegram (SPECS §3.11)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.tasks import users as U

router = APIRouter(prefix="/v1/auth/telegram", tags=["auth"])


class LinkIn(BaseModel):
    name: str | None = None
    user_id: str | None = None


@router.post("/link", status_code=201)
def link_view(payload: LinkIn):
    try:
        if payload.user_id:
            try:
                data = U.generate_link_code(payload.user_id)
                return JSONResponse(status_code=200, content=data)
            except U.NotFound:
                return JSONResponse(status_code=404, content={
                    "error": {"code": "USER_NOT_FOUND", "message": "Usuário não encontrado.", "details": {}}})
        if payload.name:
            return U.create_user(payload.name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={
            "error": {"code": "VALIDATION_ERROR", "message": str(exc), "details": {}}})
    return JSONResponse(status_code=400, content={
        "error": {"code": "VALIDATION_ERROR",
                  "message": "Informe name (novo usuário) ou user_id (regenerar código).", "details": {}}})
