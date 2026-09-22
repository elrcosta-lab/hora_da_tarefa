"""GET /suggestions (SPECS §3.8 v1.1) — escopo por dono."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.security import get_current_user_id
from app.tasks.extract import get_homework, get_suggestions, owned_by

router = APIRouter(prefix="/v1/suggestions", tags=["suggestions"])


def _err(code: str, message: str, http: int) -> JSONResponse:
    return JSONResponse(status_code=http, content={"error": {"code": code, "message": message, "details": {}}})


@router.get("", status_code=200)
def get_suggestions_view(homework_id: str, limit: int = 5, owner: str = Depends(get_current_user_id)):
    if get_homework(homework_id) is None:
        return _err("HOMEWORK_NOT_FOUND", "Tarefa não encontrada.", 404)
    if not owned_by(homework_id, owner):
        return _err("FORBIDDEN", "Sem acesso a esta tarefa.", 403)
    suggestions = get_suggestions(homework_id, limit=max(1, min(limit, 10)))
    return {"homework_id": homework_id, "suggestions": suggestions}
