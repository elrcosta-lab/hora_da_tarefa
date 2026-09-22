"""GET /suggestions (SPECS §3.8 v1.1)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.tasks.extract import get_homework, get_suggestions

router = APIRouter(prefix="/v1/suggestions", tags=["suggestions"])


@router.get("", status_code=200)
def get_suggestions_view(homework_id: str, limit: int = 5):
    if get_homework(homework_id) is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "HOMEWORK_NOT_FOUND", "message": "Tarefa não encontrada.", "details": {}}},
        )
    suggestions = get_suggestions(homework_id, limit=max(1, min(limit, 10)))
    return {"homework_id": homework_id, "suggestions": suggestions}
