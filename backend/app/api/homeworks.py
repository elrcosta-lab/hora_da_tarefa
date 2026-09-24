"""Router de upload + consulta + status (SPECS §3.2 §3.3 §3.4 §3.5 §4.6 v1.1) — escopo por dono."""
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.security import get_current_user_id
from app.core.ratelimit import limit
from app.tasks import routine as R
from app.tasks.extract import (
    StatusConflict,
    accept_suggestion,
    count_homeworks,
    detect_mime,
    export_csv,
    get_homework,
    get_or_create_homework,
    list_homeworks,
    owned_by,
    run_extraction,
    today_overview,
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


def _owned_or_error(homework_id: str, owner: str):
    if get_homework(homework_id) is None:
        return _error("HOMEWORK_NOT_FOUND", "Tarefa não encontrada.", 404)
    if not owned_by(homework_id, owner):
        return _error("FORBIDDEN", "Sem acesso a esta tarefa.", 403)
    return None


@router.post("/upload", status_code=202,
               dependencies=[Depends(limit(20, 3600, key="user", prefix="up-hour")),
                             Depends(limit(5, 60, key="user", prefix="up-min"))])
async def upload_homework(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    child_id: str = Form(...),
    hint_text: str | None = Form(default=None),
    source: str = Form(default="web"),
    owner: str = Depends(get_current_user_id),
):
    data = await file.read()
    if len(data) > MAX_BYTES:
        return _error("FILE_TOO_LARGE", "Arquivo excede 10 MB.", 413)
    mime = detect_mime(data)
    if mime not in ALLOWED:
        return _error("UNSUPPORTED_MEDIA_TYPE", "Envie JPEG, PNG ou WEBP.", 415)
    if R.get_child(child_id) is None:
        return _error("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
    if not R.owns(child_id, owner):
        return _error("FORBIDDEN", "Sem acesso a esta criança.", 403)
    rec, dedup = get_or_create_homework(data, child_id=child_id, hint_text=hint_text,
                                        created_by_user_id=owner)
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
def list_homeworks_view(child_id: str | None = None, status: str | None = None,
                        subject: str | None = None, due_before: str | None = None,
                        due_after: str | None = None, q: str | None = None,
                        sort: str = "created_at",
                        page: int = 1, page_size: int = 20,
                        owner: str = Depends(get_current_user_id)):
    if child_id is not None:
        if R.get_child(child_id) is None:
            return _error("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
        if not R.owns(child_id, owner):
            return _error("FORBIDDEN", "Sem acesso a esta criança.", 403)
    status_list = [t.strip() for t in status.split(",") if t.strip()] if status else None
    page_size = max(1, min(page_size, 100))
    # A4: total via COUNT + página via LIMIT/OFFSET (sem carregar tudo)
    total = count_homeworks(child_id=child_id, owner_user_id=owner, status=status_list,
                            subject=subject, due_before=due_before, due_after=due_after, q=q)
    start = (page - 1) * page_size
    page_items = list_homeworks(child_id=child_id, owner_user_id=owner, status=status_list,
                                subject=subject, due_before=due_before, due_after=due_after,
                                q=q, sort=sort, limit=page_size, offset=start)
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


@router.get("/today", status_code=200)
def today_view(child_id: str | None = None, date: str | None = None,
               owner: str = Depends(get_current_user_id)):
    if child_id is not None:
        if R.get_child(child_id) is None:
            return _error("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
        if not R.owns(child_id, owner):
            return _error("FORBIDDEN", "Sem acesso a esta criança.", 403)
    return today_overview(owner, child_id=child_id, date=date)


@router.get("/export", status_code=200)
def export_view(child_id: str | None = None, status: str | None = None,
                subject: str | None = None, due_before: str | None = None,
                due_after: str | None = None, q: str | None = None,
                sort: str = "created_at",
                owner: str = Depends(get_current_user_id)):
    from fastapi.responses import Response

    if child_id is not None:
        if R.get_child(child_id) is None:
            return _error("CHILD_NOT_FOUND", "Criança não encontrada.", 404)
        if not R.owns(child_id, owner):
            return _error("FORBIDDEN", "Sem acesso a esta criança.", 403)
    status_list = [t.strip() for t in status.split(",") if t.strip()] if status else None
    csv_text = export_csv(owner, child_id=child_id, status=status_list, subject=subject,
                          due_before=due_before, due_after=due_after, q=q, sort=sort)
    return Response(content="\ufeff" + csv_text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=homeworks.csv"})


@router.get("/{homework_id}/image", status_code=200)
def get_homework_image_view(homework_id: str, owner: str = Depends(get_current_user_id)):
    from fastapi.responses import Response

    from app.core.db import session_scope
    from app.core.storage import get_storage
    from app.models import HomeworkImage

    denied = _owned_or_error(homework_id, owner)
    if denied is not None:
        return denied
    with session_scope() as s:
        img = s.query(HomeworkImage).filter_by(homework_id=homework_id).first()
        if img is None:
            return _error("IMAGE_NOT_FOUND", "Imagem não encontrada.", 404)
        key, mime = img.storage_key, img.mime_type
    try:
        data = get_storage().get(key)
    except KeyError:
        return _error("IMAGE_NOT_FOUND", "Imagem não encontrada.", 404)
    return Response(content=data, media_type=mime)


@router.post("/{homework_id}/reprocess", status_code=202,
               dependencies=[Depends(limit(20, 3600, key="user", prefix="rp-hour")),
                             Depends(limit(5, 60, key="user", prefix="rp-min"))])
def reprocess_homework_view(homework_id: str, background: BackgroundTasks,
                            owner: str = Depends(get_current_user_id)):
    denied = _owned_or_error(homework_id, owner)
    if denied is not None:
        return denied
    background.add_task(run_extraction, homework_id)
    return {"homework_id": homework_id, "extraction_status": "processando"}


@router.get("/{homework_id}", status_code=200)
def get_homework_view(homework_id: str, owner: str = Depends(get_current_user_id)):
    denied = _owned_or_error(homework_id, owner)
    if denied is not None:
        return denied
    rec = get_homework(homework_id)
    return {
        "id": rec["homework_id"],
        "child_id": rec["child_id"],
        "subject": rec.get("subject"),
        "title": rec.get("title"),
        "statement": rec.get("statement"),
        "due_at": rec.get("due_at"),
        "status": rec.get("status"),
        "extraction_status": rec.get("extraction_status"),
        "needs_review": rec.get("needs_review"),
        "extraction_confidence": rec.get("confidence"),
        "estimated_minutes": rec.get("estimated_minutes"),
        "priority": rec.get("priority", 1),
    }


@router.patch("/{homework_id}/status", status_code=200)
def patch_homework_status(homework_id: str, payload: StatusPatch, owner: str = Depends(get_current_user_id)):
    denied = _owned_or_error(homework_id, owner)
    if denied is not None:
        return denied
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


class EditPayload(BaseModel):
    subject: str | None = None
    title: str | None = None
    statement: str | None = None
    due_at: str | None = None
    estimated_minutes: int | None = None
    priority: int | None = None


@router.patch("/{homework_id}", status_code=200)
def edit_homework_view(homework_id: str, payload: EditPayload, owner: str = Depends(get_current_user_id)):
    from app.tasks.extract import update_homework_fields

    denied = _owned_or_error(homework_id, owner)
    if denied is not None:
        return denied
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        return _error("VALIDATION_ERROR", "Nada para atualizar.", 400)
    try:
        rec = update_homework_fields(homework_id, **fields)
    except ValueError as exc:
        return _error("VALIDATION_ERROR", str(exc), 400)
    return {
        "id": rec["homework_id"],
        "subject": rec.get("subject"),
        "title": rec.get("title"),
        "statement": rec.get("statement"),
        "due_at": rec.get("due_at"),
        "estimated_minutes": rec.get("estimated_minutes"),
        "priority": rec.get("priority", 1),
        "extraction_status": rec.get("extraction_status"),
        "needs_review": rec.get("needs_review"),
    }


@router.post("/{homework_id}/accept", status_code=200)
def accept_homework_view(homework_id: str, payload: AcceptPayload, owner: str = Depends(get_current_user_id)):
    denied = _owned_or_error(homework_id, owner)
    if denied is not None:
        return denied
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
