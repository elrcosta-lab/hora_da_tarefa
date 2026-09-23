"""Administração: seed do admin padrão, listagem, exclusão em cascata e reset de senha."""
import os
import uuid
from datetime import datetime, timezone

from app.core.db import session_scope
from app.models import (
    Activity,
    AppUser,
    Child,
    Homework,
    HomeworkImage,
    NotificationLog,
    NotificationSetting,
    ParentAvailability,
    RefreshToken,
    SchoolSchedule,
    SuggestionSlot,
)

DEFAULT_ADMIN_EMAIL = "elrcostadev@gmail.com"


def admin_credentials() -> tuple[str, str]:
    # Default explícito pedido pelo dono (elrcostadev@gmail.com). Prefira ADMIN_*
    # no ambiente; TROQUE a senha no 1º login via reset do próprio admin.
    return (os.environ.get("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL),
            os.environ.get("ADMIN_PASSWORD", "Ui4u%80D"))


def _to_public(u: AppUser) -> dict:
    return {"user_id": u.id, "name": u.name, "email": u.email, "role": u.role or "user",
            "telegram_user_id": u.telegram_user_id,
            "created_at": u.created_at.isoformat() if u.created_at else None}


def ensure_admin(email: str | None = None, password: str | None = None) -> dict:
    """Idempotente: cria ou promove o admin; garante papel mesmo se a senha mudar."""
    from app.core.security import hash_password

    email = (email or admin_credentials()[0]).strip().lower()
    password = password if password is not None else admin_credentials()[1]
    with session_scope() as s:
        u = s.query(AppUser).filter_by(email=email).one_or_none()
        if u is None:
            u = AppUser(id=str(uuid.uuid4()), name="Administrador", email=email,
                        password_hash=hash_password(password), role="admin")
            s.add(u)
        else:
            u.role = "admin"
            u.updated_at = datetime.now(timezone.utc)
        s.flush()
        return _to_public(u)


def list_users() -> list[dict]:
    with session_scope() as s:
        return [_to_public(u) for u in s.query(AppUser).order_by(AppUser.created_at).all()]


def delete_user(user_id: str) -> dict:
    """Exclui usuário e tudo do dono. Retorna contadores."""
    with session_scope() as s:
        u = s.get(AppUser, user_id)
        if u is None:
            from app.tasks.users import NotFound

            raise NotFound(user_id)
        if u.role == "admin":
            admins = s.query(AppUser).filter_by(role="admin").count()
            if admins <= 1:
                raise ValueError("não é possível excluir o último admin")
        child_ids = [c.id for c in s.query(Child).filter_by(owner_user_id=user_id).all()]
        hw_ids = [h.id for h in s.query(Homework).filter(
            Homework.child_id.in_(child_ids)).all()] if child_ids else []
        counts = {"children": len(child_ids), "homeworks": len(hw_ids)}
        if hw_ids:
            counts["images"] = s.query(HomeworkImage).filter(
                HomeworkImage.homework_id.in_(hw_ids)).delete(synchronize_session=False)
            counts["suggestions"] = s.query(SuggestionSlot).filter(
                SuggestionSlot.homework_id.in_(hw_ids)).delete(synchronize_session=False)
            s.query(NotificationLog).filter(
                NotificationLog.homework_id.in_(hw_ids)).delete(synchronize_session=False)
            s.query(Homework).filter(Homework.id.in_(hw_ids)).delete(synchronize_session=False)
        if child_ids:
            for model in (SchoolSchedule, Activity, ParentAvailability, NotificationSetting):
                s.query(model).filter(model.child_id.in_(child_ids)).delete(synchronize_session=False)
            s.query(NotificationLog).filter(
                NotificationLog.child_id.in_(child_ids)).delete(synchronize_session=False)
            s.query(Child).filter(Child.id.in_(child_ids)).delete(synchronize_session=False)
        s.query(RefreshToken).filter_by(user_id=user_id).delete(synchronize_session=False)
        s.delete(u)
        return {"user_id": user_id, **counts}


def admin_reset_password(user_id: str, new_password: str) -> dict:
    """Define nova senha e revoga todas as sessões (refresh)."""
    from app.core.security import hash_password
    from app.tasks.users import NotFound

    if len((new_password or "").encode()) < 8:
        raise ValueError("new_password deve ter ao menos 8 caracteres")
    with session_scope() as s:
        u = s.get(AppUser, user_id)
        if u is None:
            raise NotFound(user_id)
        u.password_hash = hash_password(new_password)
        revoked = s.query(RefreshToken).filter_by(user_id=user_id).delete(synchronize_session=False)
        u.updated_at = datetime.now(timezone.utc)
        s.flush()
        return {"user_id": user_id, "sessions_revoked": revoked}
