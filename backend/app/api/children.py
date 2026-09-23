"""Children + grade + atividades (SPECS §3.6 §3.7 §3.11, RF-08) — escopo por dono."""
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.ratelimit import limit
from app.core.security import get_current_user_id
from app.tasks import routine as R

router = APIRouter(prefix="/v1/children", tags=["children"])


def _err(code: str, message: str, http: int, details: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=http,
                        content={"error": {"code": code, "message": message, "details": details or {}}})


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


@router.post("/{child_id}/routine/import", status_code=201,
             dependencies=[Depends(limit(20, 3600, key="user", prefix="imp-hour")),
                           Depends(limit(5, 60, key="user", prefix="imp-min"))])
async def import_routine_view(
    child_id: str,
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    replace: bool = Form(default=False),
    owner: str = Depends(get_current_user_id),
):
    """Importa grade+atividades+disponibilidade por inferência (texto e/ou foto)."""
    from app.api.homeworks import ALLOWED, MAX_BYTES
    from app.services.vision_openrouter import ExtractionFailed, extract_routine
    from app.tasks.extract import detect_mime

    denied = _owned_or_error(child_id, owner)
    if denied is not None:
        return denied
    image_bytes: bytes | None = None
    if file is not None:
        image_bytes = await file.read()
        if len(image_bytes) > MAX_BYTES:
            return _err("FILE_TOO_LARGE", "Arquivo excede 10 MB.", 413)
        if detect_mime(image_bytes) not in ALLOWED:
            return _err("UNSUPPORTED_MEDIA_TYPE", "Envie JPEG, PNG ou WEBP.", 415)
    if not (text or "").strip() and image_bytes is None:
        return _err("VALIDATION_ERROR", "Informe texto ou imagem da rotina.", 400)
    try:
        result = extract_routine(text=(text or None), image_bytes=image_bytes)
    except ExtractionFailed as exc:
        msg = str(exc)
        if msg.startswith("EMPTY_INPUT"):
            return _err("VALIDATION_ERROR", "Informe texto ou imagem da rotina.", 400)
        return _err("EXTRACTION_FAILED", "Não foi possível interpretar a rotina. Tente de novo.", 422,
                    details={"reason": msg[:200]})
    valid = len(result.schedules) + len(result.activities) + len(result.availability)
    if valid == 0:
        # free-tier varia: abstenção (vazio, sem warnings) merece retry; lixo, 400
        if result.warnings:
            return _err("VALIDATION_ERROR",
                        "Nenhum horário válido encontrado. Descreva dias, horas e atividades.",
                        400, details={"warnings": result.warnings})
        return _err("EXTRACTION_FAILED",
                    "A IA não entendeu. Tente de novo ou detalhe mais (dias e horas).",
                    422, details={"retryable": True})
    try:
        sched = R.save_schedules(child_id,
                                 [s.model_dump() for s in result.schedules],
                                 replace=replace)
        acts = 0
        for a in result.activities:
            R.add_activity(child_id, a.model_dump())
            acts += 1
        avail = R.save_availability(child_id,
                                    [w.model_dump() for w in result.availability],
                                    replace=replace)
    except R.Overlap as exc:
        return _err("SCHEDULE_OVERLAP", str(exc), 409)
    except R.Validation as exc:
        return _err("VALIDATION_ERROR", str(exc), 400)
    warnings = list(result.warnings) + sched.get("normalized", [])
    return {"child_id": child_id,
            "schedules_created": sched["created"],
            "activities_created": acts,
            "availability_saved": avail["created"],
            "needs_review": result.needs_review,
            "confidence": result.confidence,
            "warnings": warnings}
