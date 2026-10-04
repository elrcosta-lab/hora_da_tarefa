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


# RF-25: código de acesso da criança (8 chars Crockford base32, exibido uma vez,
# persistido só como hash Argon2id — nunca em claro, nunca em log).
CHILD_CODE_LENGTH = 8


def normalize_child_code(raw: str | None) -> str | None:
    """Normaliza código digitado: maiúsculas, sem separadores, I/L→1, O→0."""
    if not raw:
        return None
    norm = "".join(c for c in raw.strip().upper() if c.isalnum())
    norm = norm.replace("I", "1").replace("L", "1").replace("O", "0")
    if len(norm) != CHILD_CODE_LENGTH:
        return None
    return norm


def hash_child_code(code: str) -> str:
    norm = normalize_child_code(code)
    if norm is None:
        raise ValueError("código de acesso inválido")
    return _ph.hash(norm)


def verify_child_code(code: str, code_hash: str) -> bool:
    norm = normalize_child_code(code)
    if norm is None:
        return False
    try:
        return _ph.verify(code_hash, norm)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def child_code_lookup(code: str) -> str | None:
    """HMAC-SHA256 do código p/ busca O(1) indexada (correção A5).

    O pepper é o JWT_SECRET (só servidor, sem default fraco). O lookup vaza
    apenas igualdade (não o código); a autenticação continua no Argon2id.
    Rotação do JWT_SECRET invalida os lookups — regenere os códigos.
    """
    import hashlib as _hashlib
    import hmac as _hmac

    from app.core.config import get_settings

    norm = normalize_child_code(code)
    if norm is None:
        return None
    pepper = get_settings().JWT_SECRET.encode()
    return _hmac.new(pepper, norm.encode(), _hashlib.sha256).hexdigest()


def create_child_access_token(child_id: str, secret: str, minutes: int = 120) -> str:
    """JWT isolado type=child_access (somente leitura; sem refresh)."""
    now = datetime.now(timezone.utc)
    return _encode(child_id, "child_access", now + timedelta(minutes=minutes), secret)


def _encode(user_id: str, kind: str, expires: datetime, secret: str, jti: str | None = None) -> str:
    payload = {"sub": user_id, "type": kind, "exp": expires}
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, secret, algorithm="HS256")


def create_access_token(user_id: str, secret: str, access_minutes: int = 15) -> str:
    now = datetime.now(timezone.utc)
    return _encode(user_id, "access", now + timedelta(minutes=access_minutes), secret)


def create_tokens(user_id: str, secret: str, access_minutes: int = 15, refresh_days: int = 7) -> dict:
    """Legado (sem rotação). Prefira users.issue_token_pair em fluxos novos."""
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


def get_current_child_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_scheme),
) -> str:
    """RF-25: dependency isolada da criança. type=child_access; revogação rechecada no banco."""
    from app.core.config import get_settings

    if credentials is None or not credentials.credentials:
        raise Unauthorized("Bearer ausente")
    child_id = decode_token(credentials.credentials, get_settings().JWT_SECRET, expect="child_access")
    if child_id is None:
        raise Unauthorized("token inválido ou expirado")
    from app.tasks import child_access as CA

    if not CA.access_active(child_id):
        raise Unauthorized("acesso revogado")
    return child_id
