"""Auth por senha + pareamento Telegram (SPECS §3.11, §10.5)."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.security import create_tokens, decode_token, get_current_user_id
from app.tasks import users as U

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _err(code: str, message: str, http: int) -> JSONResponse:
    return JSONResponse(status_code=http, content={"error": {"code": code, "message": message, "details": {}}})


class RegisterIn(BaseModel):
    name: str
    email: str
    password: str
    lgpd_consent: bool = False
    lgpd_version: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class LinkIn(BaseModel):
    name: str | None = None
    user_id: str | None = None


def _tokens(user_id: str) -> dict:
    from app.core.config import get_settings

    s = get_settings()
    return create_tokens(user_id, s.JWT_SECRET, s.JWT_EXPIRE_MINUTES, s.REFRESH_EXPIRE_DAYS)


@router.post("/register", status_code=201)
def register_view(payload: RegisterIn):
    try:
        user = U.register(payload.name, payload.email, payload.password,
                          lgpd_consent=payload.lgpd_consent, lgpd_version=payload.lgpd_version)
    except U.ConsentRequired:
        return _err("CONSENT_REQUIRED", "É preciso aceitar o consentimento parental (LGPD).", 400)
    except U.EmailTaken:
        return _err("EMAIL_TAKEN", "E-mail já cadastrado.", 409)
    except ValueError as exc:
        return _err("VALIDATION_ERROR", str(exc), 400)
    return {"user_id": user["user_id"], "name": user["name"], "email": user["email"]}


@router.post("/login", status_code=200)
def login_view(payload: LoginIn):
    user = U.authenticate(payload.email, payload.password)
    if user is None:
        return _err("INVALID_CREDENTIALS", "E-mail ou senha inválidos.", 401)
    return {"user_id": user["user_id"], **_tokens(user["user_id"])}


@router.post("/refresh", status_code=200)
def refresh_view(payload: RefreshIn):
    from app.core.config import get_settings

    user_id = decode_token(payload.refresh_token, get_settings().JWT_SECRET, expect="refresh")
    if user_id is None or U.get_user(user_id) is None:
        return _err("INVALID_TOKEN", "Refresh inválido ou expirado.", 401)
    return {"user_id": user_id, **_tokens(user_id)}


@router.post("/telegram/link", status_code=201)
def link_view(payload: LinkIn, current_user_id: str = Depends(get_current_user_id)):
    try:
        if payload.user_id:
            if payload.user_id != current_user_id:
                return _err("FORBIDDEN", "Código só para a própria conta.", 403)
            try:
                data = U.generate_link_code(payload.user_id)
                return JSONResponse(status_code=200, content=data)
            except U.NotFound:
                return _err("USER_NOT_FOUND", "Usuário não encontrado.", 404)
        if payload.name:
            return U.create_user(payload.name)
    except ValueError as exc:
        return _err("VALIDATION_ERROR", str(exc), 400)
    return _err("VALIDATION_ERROR", "Informe name (novo usuário) ou user_id (regenerar código).", 400)
