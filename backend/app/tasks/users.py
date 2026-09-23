"""Usuários + pareamento Telegram (SPECS §3.11, §6.1).

Acesso ao bot EXCLUSIVO para telegram_user_id vinculados. Código de 6 dígitos
single-use gerado em POST /auth/telegram/link e consumido no chat.
"""
import secrets
import uuid
from datetime import datetime, timezone

from app.core.db import session_scope
from app.models import AppUser


class NotFound(Exception):
    pass


class EmailTaken(Exception):
    pass


class ConsentRequired(Exception):
    pass


def _to_dict(u: AppUser) -> dict:
    return {"user_id": u.id, "name": u.name, "email": u.email, "role": u.role or "user",
            "telegram_user_id": u.telegram_user_id,
            "link_code": u.telegram_link_code,
            "lgpd_consent_at": u.lgpd_consent_at.isoformat() if u.lgpd_consent_at else None,
            "lgpd_consent_version": u.lgpd_consent_version}


def get_user(user_id: str) -> dict | None:
    with session_scope() as s:
        u = s.get(AppUser, user_id)
        return _to_dict(u) if u else None


def register(name: str, email: str, password: str, lgpd_consent: bool = False,
             lgpd_version: str | None = None) -> dict:
    from app.core.security import hash_password

    if not lgpd_consent:
        raise ConsentRequired("consentimento parental (LGPD) é obrigatório")
    if not (name or "").strip():
        raise ValueError("name é obrigatório")
    email = (email or "").strip().lower()
    if "@" not in email:
        raise ValueError("e-mail inválido")
    with session_scope() as s:
        if s.query(AppUser).filter_by(email=email).one_or_none() is not None:
            raise EmailTaken(email)
        u = AppUser(id=str(uuid.uuid4()), name=name.strip(), email=email,
                    password_hash=hash_password(password),
                    lgpd_consent_at=datetime.now(timezone.utc),
                    lgpd_consent_version=lgpd_version or "termos-v1")
        _stamp_code(u, s)
        s.add(u)
        s.flush()
        return _to_dict(u)


def authenticate(email: str, password: str) -> dict | None:
    from app.core.security import verify_password

    with session_scope() as s:
        u = s.query(AppUser).filter_by(email=(email or "").strip().lower()).one_or_none()
        if u is None or not u.password_hash:
            return None
        if not verify_password(password, u.password_hash):
            return None
        return _to_dict(u)


LINK_TTL_MINUTES = 15


def _new_code(s, length: int = 6) -> str:
    for _ in range(20):
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        if s.query(AppUser).filter_by(telegram_link_code=code).one_or_none() is None:
            return code
    raise RuntimeError("não foi possível gerar link_code único")


def _stamp_code(u, s) -> None:
    from datetime import timedelta

    u.telegram_link_code = _new_code(s)
    u.link_expires_at = datetime.now(timezone.utc) + timedelta(minutes=LINK_TTL_MINUTES)
    u.updated_at = datetime.now(timezone.utc)


def create_user(name: str) -> dict:
    if not (name or "").strip():
        raise ValueError("name é obrigatório")
    with session_scope() as s:
        u = AppUser(id=str(uuid.uuid4()), name=name.strip())
        _stamp_code(u, s)
        s.add(u)
        s.flush()
        return _to_dict(u)


def generate_link_code(user_id: str) -> dict:
    with session_scope() as s:
        u = s.get(AppUser, user_id)
        if u is None:
            raise NotFound(user_id)
        _stamp_code(u, s)
        s.flush()
        return _to_dict(u)


def get_by_telegram_id(telegram_user_id: int) -> dict | None:
    with session_scope() as s:
        u = s.query(AppUser).filter_by(telegram_user_id=int(telegram_user_id)).one_or_none()
        return _to_dict(u) if u else None


def link_telegram(code: str, telegram_user_id: int) -> dict | None:
    """Consome o código (single-use, 15 min) e vincula o chat. Retorna None se inválido/expirado."""
    code = (code or "").strip()
    if not code:
        return None
    with session_scope() as s:
        u = s.query(AppUser).filter_by(telegram_link_code=code).one_or_none()
        if u is None:
            return None
        exp = u.link_expires_at
        if exp is not None:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= datetime.now(timezone.utc):
                u.telegram_link_code = None
                u.link_expires_at = None
                s.flush()
                return None
        u.telegram_user_id = int(telegram_user_id)
        u.telegram_link_code = None
        u.link_expires_at = None
        u.updated_at = datetime.now(timezone.utc)
        s.flush()
        return _to_dict(u)


def clear_users() -> None:
    """Apenas testes."""
    from app.models import RefreshToken

    with session_scope() as s:
        s.query(RefreshToken).delete()
        s.query(AppUser).delete()


def _hash_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def issue_token_pair(user_id: str) -> dict:
    """Emite access + refresh single-use (jti = linha da tabela)."""
    import uuid as _uuid
    from datetime import timedelta as _td

    from app.core.config import get_settings
    from app.core.security import _encode, create_access_token
    from app.models import RefreshToken

    settings = get_settings()
    now = datetime.now(timezone.utc)
    jti = str(_uuid.uuid4())
    refresh = _encode(user_id, "refresh", now + _td(days=settings.REFRESH_EXPIRE_DAYS),
                      settings.JWT_SECRET, jti=jti)
    with session_scope() as s:
        s.add(RefreshToken(id=jti, user_id=user_id, token_hash=_hash_token(refresh),
                           revoked=False,
                           expires_at=now + _td(days=settings.REFRESH_EXPIRE_DAYS)))
        s.flush()
    return {"access_token": create_access_token(user_id, settings.JWT_SECRET,
                                                settings.JWT_EXPIRE_MINUTES),
            "refresh_token": refresh, "token_type": "bearer"}


def rotate_refresh(refresh_token: str) -> dict | None:
    """Consome o refresh (single-use) e emite par novo. Reuso → revoga a família (roubo)."""
    import jwt as _jwt

    from app.core.config import get_settings
    from app.models import RefreshToken

    settings = get_settings()
    try:
        payload = _jwt.decode(refresh_token, settings.JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None
    if payload.get("type") != "refresh":
        return None
    jti, user_id = payload.get("jti"), payload.get("sub")
    if not jti or not user_id:
        return None
    with session_scope() as s:
        row = s.get(RefreshToken, jti)
        if row is None or row.user_id != user_id or row.token_hash != _hash_token(refresh_token):
            return None
        if row.revoked:
            # reuso detectado: possível roubo — revoga tudo do usuário
            s.query(RefreshToken).filter_by(user_id=user_id).update({"revoked": True})
            return None
        if row.expires_at.tzinfo is None:
            exp = row.expires_at.replace(tzinfo=timezone.utc)
        else:
            exp = row.expires_at
        if exp <= datetime.now(timezone.utc):
            return None
        row.revoked = True
        s.flush()
    return issue_token_pair(user_id)
