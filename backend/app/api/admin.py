"""Gestão administrativa (RBAC: role=admin)."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.security import get_current_user_id
from app.tasks import admin as A
from app.tasks import users as U

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _err(code: str, message: str, http: int) -> JSONResponse:
    return JSONResponse(status_code=http, content={"error": {"code": code, "message": message, "details": {}}})


def require_admin(current_user_id: str = Depends(get_current_user_id)) -> str:
    user = U.get_user(current_user_id)
    if user is None:
        from app.core.security import Unauthorized

        raise Unauthorized("usuário inexistente")
    if user.get("role") != "admin":
        raise PermissionError("admin required")
    return current_user_id


@router.get("/users", status_code=200)
def admin_list_users(admin_id: str = Depends(require_admin)):
    _ = admin_id
    items = A.list_users()
    return {"items": items, "total": len(items)}


@router.delete("/users/{user_id}", status_code=200)
def admin_delete_user(user_id: str, admin_id: str = Depends(require_admin)):
    if user_id == admin_id:
        return _err("SELF_DELETE", "Não é possível excluir a própria conta de admin.", 409)
    try:
        return {"deleted": A.delete_user(user_id)}
    except U.NotFound:
        return _err("USER_NOT_FOUND", "Usuário não encontrado.", 404)
    except ValueError as exc:
        return _err("VALIDATION_ERROR", str(exc), 409)


class ResetIn(BaseModel):
    new_password: str


@router.post("/users/{user_id}/reset-password", status_code=200)
def admin_reset_password(user_id: str, payload: ResetIn, admin_id: str = Depends(require_admin)):
    _ = admin_id
    try:
        return A.admin_reset_password(user_id, payload.new_password)
    except U.NotFound:
        return _err("USER_NOT_FOUND", "Usuário não encontrado.", 404)
    except ValueError as exc:
        return _err("VALIDATION_ERROR", str(exc), 400)
