"""Router de upload + consulta + status (SPECS §3.2 §3.3 §3.4 §3.5 §4.6 v1.1)."""
from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.tasks.extract import (
    StatusConflict,
    accept_suggestion,
    detect_mime,
    get_homework,
    get_or_create_homework,
    list_homeworks,
    run_extraction,
    transition_homework,
)

router = APIRouter(prefix="/v1/homeworks", tags=["homeworks"])

MAX_BYTES = 10 * 1024 * 1024
ALLOWED = {"image/jpeg", "image/png", "image/webp"}


def _error(code: str, message: str, http: int, details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=http, content={"error": {"code": code, "message": message, "details": details or {}}}
    )


class StatusPatch(BaseModel):
    status: str
    reason: str | None = None


@router.post("/upload", status_code=202)
async def upload_homework(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    child_id: str = Form(...),
    hint_text: str | None = Form(default=None),
    source: str = Form(default="web"),
):
    data = await file.read()
    if len(data) > MAX_BYTES:
        return _error("FILE_TOO_LARGE", "Arquivo excede 10 MB.", 413)
    mime = detect_mime(data)
    if mime not in ALLOWED:
        return _error("UNSUPPORTED_MEDIA_TYPE", "Envie JPEG, PNG ou WEBP.", 415)
    rec, dedup = get_or_create_homework(data, child_id=child_id, hint_text=hint_text)
    if not dedup:
        background.add_task(run_extraction, rec["homework_id"])
    body = {
        "homework_id": rec["homework_id"],
        "child_id": rec["child_id"],
        "status": rec["status"],
        "extraction_status": rec["extraction_status"],
        "message": "Tarefa recebida. Processamento iniciado.",
    }
    if dedup:
        body["deduplicated"] = True
    return JSONResponse(status_code=202, content=body)


@router.get("", status_code=200)
def list_homeworks_view(child_id: str | None = None, page: int = 1, page_size: int = 20):
    page_size = max(1, min(page_size, 100))
    items = list_homeworks(child_id=child_id)
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    return {
        "items": [
            {
                "id": r["homework_id"],
                "child_id": r["child_id"],
                "subject": r.get("subject"),
                "title": r.get("title"),
                "due_at": r.get("due_at"),
                "status": r.get("status"),
                "extraction_status": r.get("extraction_status"),
                "extraction_confidence": r.get("confidence"),
                "estimated_minutes": r.get("estimated_minutes"),
                "priority": r.get("priority", 1),
            }
            for r in page_items
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/{homework_id}", status_code=200)
def get_homework_view(homework_id: str):
    rec = get_homework(homework_id)
    if rec is None:
        return _error("HOMEWORK_NOT_FOUND", "Tarefa não encontrada.", 404)
    return {
        "id": rec["homework_id"],
        "child_id": rec["child_id"],
        "subject": rec.get("subject"),
        "title": rec.get("title"),
        "statement": rec.get("statement"),
        "due_at": rec.get("due_at"),
        "status": rec.get("status"),
        "extraction_status": rec.get("extraction_status"),
        "extraction_confidence": rec.get("confidence"),
        "estimated_minutes": rec.get("estimated_minutes"),
        "priority": rec.get("priority", 1),
    }


@router.patch("/{homework_id}/status", status_code=200)
def patch_homework_status(homework_id: str, payload: StatusPatch):
    if get_homework(homework_id) is None:
        return _error("HOMEWORK_NOT_FOUND", "Tarefa não encontrada.", 404)
    try:
        rec = transition_homework(homework_id, payload.status)
    except StatusConflict as exc:
        return _error(
            "STATUS_CONFLICT",
            f"Transição {exc.current} -> {exc.attempted} inválida.",
            409,
            details={"current": exc.current, "attempted": exc.attempted, "allowed": exc.allowed},
        )
    return {"id": rec["homework_id"], "status": rec["status"], "updated_at": rec.get("updated_at")}


class AcceptPayload(BaseModel):
    start_at: str


@router.post("/{homework_id}/accept", status_code=200)
def accept_homework_view(homework_id: str, payload: AcceptPayload):
    if get_homework(homework_id) is None:
        return _error("HOMEWORK_NOT_FOUND", "Tarefa não encontrada.", 404)
    try:
        rec = accept_suggestion(homework_id, payload.start_at)
    except StatusConflict as exc:
        return _error(
            "STATUS_CONFLICT",
            "Slot inválido ou fora das sugestões atuais.",
            409,
            details={"current": exc.current, "attempted": exc.attempted, "allowed": exc.allowed},
        )
    return {
        "homework_id": rec["homework_id"],
        "status": rec["status"],
        "scheduled_start": rec.get("scheduled_start"),
        "scheduled_end": rec.get("scheduled_end"),
    }
