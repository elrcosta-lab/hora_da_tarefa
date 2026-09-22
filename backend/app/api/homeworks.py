"""Router de upload (SPECS §3.2 v1.1)."""
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app.tasks.extract import detect_mime, get_or_create_homework

router = APIRouter(prefix="/v1/homeworks", tags=["homeworks"])

MAX_BYTES = 10 * 1024 * 1024
ALLOWED = {"image/jpeg", "image/png", "image/webp"}


def _error(code: str, message: str, http: int) -> JSONResponse:
    return JSONResponse(status_code=http, content={"error": {"code": code, "message": message, "details": {}}})


@router.post("/upload", status_code=202)
async def upload_homework(
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
