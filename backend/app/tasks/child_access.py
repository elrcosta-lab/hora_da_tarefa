"""Acesso da criança por código (RF-25, SPECS §2.14 §3.12).

Código de 8 chars Crockford base32 por criança, persistente e revogável,
persistido SOMENTE como hash Argon2id. Sem PII nova: segredo operacional
de visualização somente leitura (não credencial de identidade do menor).
Retorna sempre dicts simples (nunca ORM detached).
"""
import secrets
from datetime import datetime, timezone

from app.core.db import session_scope
from app.core.security import child_code_lookup, hash_child_code, verify_child_code
from app.models import Child, ChildAccess

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (sem I, L, O, U)


class NotFound(Exception):
    pass


class NoAccessCode(Exception):
    pass


def _generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _to_meta(row: ChildAccess) -> dict:
    return {"child_id": row.child_id, "active": not row.revoked, "revoked": row.revoked,
            "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def upsert_access_code(child_id: str) -> dict:
    """Gera (ou regenera) o código. Retorna o plaintext UMA única vez."""
    with session_scope() as s:
        c = s.get(Child, child_id)
        if c is None:
            raise NotFound(child_id)
        code = _generate_code()
        row = s.get(ChildAccess, child_id)
        now = datetime.now(timezone.utc)
        lookup = child_code_lookup(code)
        assert lookup is not None  # gerado sempre normaliza
        if row is None:
            row = ChildAccess(child_id=child_id, code_hash=hash_child_code(code),
                              code_lookup=lookup,
                              revoked=False, created_at=now, updated_at=now)
            s.add(row)
        else:
            row.code_hash = hash_child_code(code)
            row.code_lookup = lookup
            row.revoked = False
            row.updated_at = now
        s.flush()
        return {"child_id": child_id, "code": code,
                "created_at": row.created_at.isoformat() if row.created_at else None}


def get_access_code(child_id: str) -> dict | None:
    """Metadados do acesso. Nunca retorna code/code_hash. None se nunca criado."""
    with session_scope() as s:
        if s.get(Child, child_id) is None:
            raise NotFound(child_id)
        row = s.get(ChildAccess, child_id)
        return _to_meta(row) if row else None


def revoke_access_code(child_id: str) -> dict:
    with session_scope() as s:
        if s.get(Child, child_id) is None:
            raise NotFound(child_id)
        row = s.get(ChildAccess, child_id)
        if row is None:
            raise NoAccessCode(child_id)
        row.revoked = True
        row.updated_at = datetime.now(timezone.utc)
        s.flush()
        return {"child_id": child_id, "revoked": True}


def access_active(child_id: str) -> bool:
    """Criança ativa + acesso não revogado. Rechecado a cada request (revogação imediata)."""
    with session_scope() as s:
        c = s.get(Child, child_id)
        if c is None or not c.active:
            return False
        row = s.get(ChildAccess, child_id)
        return row is not None and not row.revoked


def child_public(child_id: str) -> dict | None:
    """Campos mínimos para a área da criança (sem PII além de nome/série)."""
    with session_scope() as s:
        c = s.get(Child, child_id)
        if c is None:
            return None
        return {"id": c.id, "name": c.name, "grade_level": c.grade_level}


def login_by_code(code: str) -> dict | None:
    """Valida o código e retorna o dict público da criança. None = genérico (anti-oráculo).

    A5: busca O(1) pelo `code_lookup` (HMAC indexado) + 1 verify Argon2id.
    Linhas legadas (`code_lookup=""`, pré-0014) usam varredura restrita só a elas.
    """
    lookup = child_code_lookup(code)
    if lookup is None:
        return None

    def _hit(access: ChildAccess, child: Child) -> dict:
        access.last_login_at = datetime.now(timezone.utc)
        access.updated_at = access.last_login_at
        return {"id": child.id, "name": child.name, "grade_level": child.grade_level}

    with session_scope() as s:
        match = (s.query(ChildAccess, Child)
                 .join(Child, Child.id == ChildAccess.child_id)
                 .filter(ChildAccess.code_lookup == lookup,
                         ChildAccess.revoked.is_(False), Child.active.is_(True))
                 .first())
        if match is not None:
            access, child = match
            if verify_child_code(code, access.code_hash):
                result = _hit(access, child)
                s.flush()
                return result
            return None
        # fallback legado: só linhas sem lookup (pré-0014)
        legacy = (s.query(ChildAccess, Child)
                  .join(Child, Child.id == ChildAccess.child_id)
                  .filter(ChildAccess.code_lookup == "",
                          ChildAccess.revoked.is_(False), Child.active.is_(True))
                  .all())
        for access, child in legacy:
            if verify_child_code(code, access.code_hash):
                result = _hit(access, child)
                s.flush()
                return result
        return None


def clear_child_access() -> None:
    """Apenas testes."""
    with session_scope() as s:
        s.query(ChildAccess).delete()
