"""Segurança: Argon2id + JWT (SPECS §10.5, RNF-06)."""
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_ph = PasswordHasher()
_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    if len(password.encode()) < 8:
        raise ValueError("password deve ter ao menos 8 caracteres")
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def _encode(user_id: str, kind: str, expires: datetime, secret: str) -> str:
    return jwt.encode({"sub": user_id, "type": kind, "exp": expires}, secret, algorithm="HS256")


def create_tokens(user_id: str, secret: str, access_minutes: int = 15, refresh_days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "access_token": _encode(user_id, "access", now + timedelta(minutes=access_minutes), secret),
        "refresh_token": _encode(user_id, "refresh", now + timedelta(days=refresh_days), secret),
        "token_type": "bearer",
    }


def decode_token(token: str, secret: str, expect: str = "access") -> str | None:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        return None
    if payload.get("type") != expect:
        return None
    return payload.get("sub")


class Unauthorized(Exception):
    pass


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_scheme),
) -> str:
    from app.core.config import get_settings

    if credentials is None or not credentials.credentials:
        raise Unauthorized("Bearer ausente")
    user_id = decode_token(credentials.credentials, get_settings().JWT_SECRET, expect="access")
    if user_id is None:
        raise Unauthorized("token inválido ou expirado")
    from app.tasks import users as U

    if U.get_user(user_id) is None:
        raise Unauthorized("usuário inexistente")
    return user_id
