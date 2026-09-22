"""TDD RED — POST /v1/homeworks/upload (SPECS §3.2 v1.1).

Contrato:
- multipart file + child_id + hint_text opcional
- allowlist jpeg/png/webp por magic bytes, ≤10MB
- 202 {homework_id, child_id, status:pendente, extraction_status:processando}
- 415 mime fora da allowlist, 413 >10MB
- dedupe por SHA-256: mesmo arquivo → mesmo homework_id sem reprocessar
"""
import io
import uuid

from fastapi.testclient import TestClient
from PIL import Image


def _img_bytes(fmt="JPEG", size=(800, 600)):
    img = Image.new("RGB", size, (255, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_upload_accepts_jpeg_and_returns_202():
    from app.main import app

    client = TestClient(app)
    child_id = str(uuid.uuid4())
    resp = client.post(
        "/v1/homeworks/upload",
        files={"file": ("tarefa.jpg", _img_bytes(), "image/jpeg")},
        data={"child_id": child_id, "hint_text": "é de matemática"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["child_id"] == child_id
    assert body["status"] == "pendente"
    assert body["extraction_status"] == "processando"
    assert "homework_id" in body


def test_upload_rejects_unsupported_media_type():
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/v1/homeworks/upload",
        files={"file": ("tarefa.txt", b"nao eh imagem", "text/plain")},
        data={"child_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 415


def test_upload_dedupes_same_sha256():
    from app.main import app

    client = TestClient(app)
    child_id = str(uuid.uuid4())
    payload = _img_bytes()
    r1 = client.post(
        "/v1/homeworks/upload",
        files={"file": ("a.jpg", payload, "image/jpeg")},
        data={"child_id": child_id},
    )
    r2 = client.post(
        "/v1/homeworks/upload",
        files={"file": ("a.jpg", payload, "image/jpeg")},
        data={"child_id": child_id},
    )
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["homework_id"] == r2.json()["homework_id"]
    assert r2.json().get("deduplicated") is True
