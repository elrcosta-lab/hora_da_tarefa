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
    return {"user_id": u.id, "name": u.name, "email": u.email,
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
                    telegram_link_code=_new_code(s),
                    lgpd_consent_at=datetime.now(timezone.utc),
                    lgpd_consent_version=lgpd_version or "termos-v1")
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


def _new_code(s, length: int = 6) -> str:
    for _ in range(20):
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        if s.query(AppUser).filter_by(telegram_link_code=code).one_or_none() is None:
            return code
    raise RuntimeError("não foi possível gerar link_code único")


def create_user(name: str) -> dict:
    if not (name or "").strip():
        raise ValueError("name é obrigatório")
    with session_scope() as s:
        u = AppUser(id=str(uuid.uuid4()), name=name.strip(),
                    telegram_link_code=_new_code(s))
        s.add(u)
        s.flush()
        return _to_dict(u)


def generate_link_code(user_id: str) -> dict:
    with session_scope() as s:
        u = s.get(AppUser, user_id)
        if u is None:
            raise NotFound(user_id)
        u.telegram_link_code = _new_code(s)
        u.updated_at = datetime.now(timezone.utc)
        s.flush()
        return _to_dict(u)


def get_by_telegram_id(telegram_user_id: int) -> dict | None:
    with session_scope() as s:
        u = s.query(AppUser).filter_by(telegram_user_id=int(telegram_user_id)).one_or_none()
        return _to_dict(u) if u else None


def link_telegram(code: str, telegram_user_id: int) -> dict | None:
    """Consome o código (single-use) e vincula o chat. Retorna None se inválido."""
    code = (code or "").strip()
    if not code:
        return None
    with session_scope() as s:
        u = s.query(AppUser).filter_by(telegram_link_code=code).one_or_none()
        if u is None:
            return None
        u.telegram_user_id = int(telegram_user_id)
        u.telegram_link_code = None
        u.updated_at = datetime.now(timezone.utc)
        s.flush()
        return _to_dict(u)


def clear_users() -> None:
    """Apenas testes."""
    with session_scope() as s:
        s.query(AppUser).delete()
