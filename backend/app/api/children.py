"""Children + grade + atividades (SPECS §3.6 §3.7 §3.11, RF-08) — escopo por dono."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.security import get_current_user_id
from app.tasks import routine as R

router = APIRouter(prefix="/v1/children", tags=["children"])


def _err(code: str, message: str, http: int) -> JSONResponse:
    return JSONResponse(status_code=http, content={"error": {"code": code, "message": message, "details": {}}})


def _owned_or_error(child_id: str, owner: str):
    if R.get_child(child_id) is None:
        return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
    if not R.owns(child_id, owner):
        return _err("FORBIDDEN", "Sem acesso a esta criança.", 403)
    return None


class ChildIn(BaseModel):
    name: str
    grade_level: str | None = None
    school_name: str | None = None
    birth_date: str | None = None
    timezone: str = "America/Sao_Paulo"


class SchedulesIn(BaseModel):
    replace: bool = False
    entries: list[dict] = []


class ActivityIn(BaseModel):
    title: str
    weekday: int | None = None
    start_time: str
    end_time: str
    recurrence: str = "weekly"
    travel_before_min: int = 0
    travel_after_min: int = 0
    is_blocking: bool = True
    location: str | None = None


@router.post("", status_code=201)
def create_child_view(payload: ChildIn, owner: str = Depends(get_current_user_id)):
    try:
        rec = R.create_child(payload.name, payload.grade_level, payload.school_name,
                             payload.birth_date, payload.timezone, owner_user_id=owner)
    except R.Validation as exc:
        return _err("VALIDATION_ERROR", str(exc), 400)
    return rec


@router.get("", status_code=200)
def list_children_view(owner: str = Depends(get_current_user_id)):
    return {"items": R.list_children(owner_user_id=owner)}


@router.post("/{child_id}/schedules", status_code=201)
def save_schedules_view(child_id: str, payload: SchedulesIn, owner: str = Depends(get_current_user_id)):
    denied = _owned_or_error(child_id, owner)
    if denied is not None:
        return denied
    try:
        return R.save_schedules(child_id, payload.entries, replace=payload.replace)
    except R.NotFound:
        return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
    except R.Overlap as exc:
        return _err("SCHEDULE_OVERLAP", str(exc), 409)
    except R.Validation as exc:
        return _err("VALIDATION_ERROR", str(exc), 400)


@router.post("/{child_id}/activities", status_code=201)
def add_activity_view(child_id: str, payload: ActivityIn, owner: str = Depends(get_current_user_id)):
    denied = _owned_or_error(child_id, owner)
    if denied is not None:
        return denied
    try:
        return R.add_activity(child_id, payload.model_dump())
    except R.NotFound:
        return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
    except R.Validation as exc:
        return _err("VALIDATION_ERROR", str(exc), 400)


@router.get("/{child_id}/agenda", status_code=200)
def get_agenda_view(child_id: str, owner: str = Depends(get_current_user_id)):
    denied = _owned_or_error(child_id, owner)
    if denied is not None:
        return denied
    try:
        return R.get_agenda(child_id)
    except R.NotFound:
        return _err("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
