"""TDD RED — Rate limiting (SPECS §10.7, CA-06).

Limites: upload 20/h + burst 5/min; login 10/15min; webhook 120/min/IP;
global 300/min por usuário. 429 RATE_LIMITED com Retry-After.
"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_auth


def _img_bytes(seed=0):
    img = Image.new("RGB", (800 + seed, 600), (70, 80, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_limiter_allows_then_denies_with_retry_after():
    from app.core.ratelimit import Limiter

    lim = Limiter(memory_only=True)
    for _ in range(3):
        allowed, _ = lim.hit("k1", limit=3, window_s=60)
        assert allowed is True
    allowed, retry = lim.hit("k1", limit=3, window_s=60)
    assert allowed is False
    assert retry > 0


def test_upload_burst_5_per_min():
    from app.main import app

    client = TestClient(app)
    h, _ = make_auth(client)
    cid = client.post("/v1/children", json={"name": "Ana"}, headers=h).json()["id"]
    codes = []
    for i in range(6):
        r = client.post("/v1/homeworks/upload",
                        files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(seed=100 + i), "image/jpeg")},
                        data={"child_id": cid}, headers=h)
        codes.append(r.status_code)
    assert codes[:5] == [202] * 5
    assert codes[5] == 429
    body = client.post("/v1/homeworks/upload",
                       files={"file": (f"{uuid.uuid4()}.jpg", _img_bytes(seed=200), "image/jpeg")},
                       data={"child_id": cid}, headers=h)
    assert body.json()["error"]["code"] == "RATE_LIMITED"
    assert "retry-after" in {k.lower() for k in body.headers}


def test_login_bruteforce_blocked():
    from app.main import app

    client = TestClient(app)
    email = f"victim-{uuid.uuid4().hex[:8]}@teste.com"
    r = client.post("/v1/auth/register", json={"name": "V", "email": email, "password": "senha-forte-123",
                                               "lgpd_consent": True})
    assert r.status_code == 201
    last = None
    for _ in range(11):
        last = client.post("/v1/auth/login", json={"email": email, "password": "errada-errada-errada"})
    assert last.status_code == 429
    assert last.json()["error"]["code"] == "RATE_LIMITED"


def test_global_limit_per_user(monkeypatch):
    monkeypatch.setenv("RATE_GLOBAL_PER_MIN", "5")
    from app.main import app

    client = TestClient(app)
    h, _ = make_auth(client)
    codes = [client.get("/v1/children", headers=h).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429
