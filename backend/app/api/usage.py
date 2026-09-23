"""Uso e custo estimado de IA por conta (visibilidade de gasto)."""
from fastapi import APIRouter, Depends

from app.core.security import get_current_user_id
from app.tasks.extract import usage_summary

router = APIRouter(prefix="/v1/usage", tags=["usage"])


@router.get("", status_code=200)
def usage_view(owner: str = Depends(get_current_user_id)):
    return usage_summary(owner)
