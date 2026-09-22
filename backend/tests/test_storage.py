"""TDD RED — StorageProvider (SPECS §8.2, RNF-09/11).

Contrato: put/get/delete por chave opaca; backend `local` (disco, dev/testes)
ou `s3` (MinIO/prod, S3-compatible). Upload persiste HomeworkImage + bytes
fora da memória de processo.
"""
import os

import pytest


@pytest.fixture()
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "images"))
    from app.core.storage import get_storage

    return get_storage()


def test_local_roundtrip_put_get_delete(local_storage):
    key = local_storage.put(f"hw/{'x' * 8}.jpg", b"\xff\xd8\xff fake-jpeg", "image/jpeg")
    assert local_storage.get(key) == b"\xff\xd8\xff fake-jpeg"
    local_storage.delete(key)
    with pytest.raises(KeyError):
        local_storage.get(key)


def test_local_rejects_path_traversal(local_storage):
    with pytest.raises(ValueError):
        local_storage.put("../../etc/evil.jpg", b"evil", "image/jpeg")


def test_upload_persists_bytes_outside_process_memory(tmp_path, monkeypatch):
    """Prova que run_extraction NÃO depende de _IMAGES: limpa o cache e reexecuta."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "images"))
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from app.main import app
    from app.schemas.extraction import ExtractionResult
    from app.tasks import extract as E

    client = TestClient(app)
    assert not hasattr(E, "_IMAGES"), "cache _IMAGES em memória deve ter sido removido"

    import io
    import uuid
    from PIL import Image

    img = Image.new("RGB", (800, 600), (9, 9, 9))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    ok = ExtractionResult(is_homework=True, subject="Mat", title="T", statement="S",
                          due_at="2026-09-25", estimated_minutes=30, priority=1,
                          confidence=0.9, needs_review=False, extraction_status="ok", meta={})
    with patch("app.services.vision_openrouter.extract_homework", return_value=ok) as m:
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", buf.getvalue(), "image/jpeg")},
                        data={"child_id": str(uuid.uuid4())})
        assert r.status_code == 202
        # background do TestClient já rodou a extração via storage
        assert m.call_count == 1

    from app.tasks.extract import get_homework

    rec = get_homework(r.json()["homework_id"])
    assert rec["subject"] == "Mat"
    assert rec["extraction_status"] == "ok"


def test_purge_expired_images_removes_bytes_and_row(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "images"))
    from datetime import datetime, timezone

    from app.core.storage import get_storage
    from app.tasks.extract import get_or_create_homework, purge_expired_images

    rec, _ = get_or_create_homework(b"\xff\xd8\xff purge-me", child_id="kid-1")
    from app.core.db import session_scope
    from app.models import HomeworkImage

    with session_scope() as s:
        img = s.query(HomeworkImage).filter_by(homework_id=rec["homework_id"]).one()
        img.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        key = img.storage_key
    removed = purge_expired_images(now=datetime(2026, 9, 22, tzinfo=timezone.utc))
    assert removed == 1
    with pytest.raises(KeyError):
        get_storage().get(key)


def test_s3_storage_skipped_without_minio():
    from app.core.storage import S3Storage

    if os.environ.get("MINIO_TEST_URL"):
        s = S3Storage(endpoint=os.environ["MINIO_TEST_URL"], bucket="test",
                      access_key="minioadmin", secret_key="minioadmin123")
        key = s.put("ping.txt", b"pong", "text/plain")
        assert s.get(key) == b"pong"
        s.delete(key)
    else:
        pytest.skip("MINIO_TEST_URL ausente — live S3 testado no compose")
