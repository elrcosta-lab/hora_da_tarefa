"""Área da criança (RF-25, SPECS §3.12) — somente leitura, escopo pelo token.

Todo filtro usa o child_id do JWT (get_current_child_id); não há child_id
de entrada, logo cross-child é impossível por construção. Sem mutação:
qualquer POST/PATCH/DELETE aqui responde 405.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.security import get_current_child_id
from app.tasks import child_access as CA
from app.tasks.extract import count_homeworks, list_homeworks

router = APIRouter(prefix="/v1/child", tags=["child"])


def _err(code: str, message: str, http: int) -> JSONResponse:
    return JSONResponse(status_code=http, content={"error": {"code": code, "message": message, "details": {}}})


@router.get("/me", status_code=200)
def child_me_view(child_id: str = Depends(get_current_child_id)):
    pub = CA.child_public(child_id)
    if pub is None:
        return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
    return pub


@router.get("/homeworks", status_code=200)
def child_homeworks_view(status: str | None = None, subject: str | None = None,
                         q: str | None = None, sort: str = "created_at",
                         page: int = 1, page_size: int = 20,
                         child_id: str = Depends(get_current_child_id)):
    status_list = [t.strip() for t in status.split(",") if t.strip()] if status else None
    page_size = max(1, min(page_size, 100))
    total = count_homeworks(child_id=child_id, owner_user_id=None, status=status_list,
                            subject=subject, q=q)
    start = (page - 1) * page_size
    page_items = list_homeworks(child_id=child_id, owner_user_id=None, status=status_list,
                                subject=subject, q=q, sort=sort, limit=page_size, offset=start)
    return {
        "items": [
            {
                "id": r["homework_id"],
                "child_id": r["child_id"],
                "subject": r.get("subject"),
                "title": r.get("title"),
                "statement": r.get("statement"),
                "due_at": r.get("due_at"),
                "status": r.get("status"),
                "scheduled_start": r.get("scheduled_start"),
                "scheduled_end": r.get("scheduled_end"),
                "estimated_minutes": r.get("estimated_minutes"),
                "priority": r.get("priority", 1),
            }
            for r in page_items
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }
