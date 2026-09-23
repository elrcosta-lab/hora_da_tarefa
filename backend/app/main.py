"""FastAPI app (SPECS §3.1 v1.1)."""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.children import router as children_router
from app.api.homeworks import router as homeworks_router
from app.api.notifications import router as notifications_router
from app.api.suggestions import router as suggestions_router
from app.api.telegram import router as telegram_router
from app.api.usage import router as usage_router
from app.core.security import Unauthorized
from app.core.ratelimit import RateLimited, get_limiter, global_limit
from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.core.db import init_db
    from app.tasks.admin import ensure_admin
    from app.tasks.beat import start_scheduler

    init_db()
    try:
        ensure_admin()
    except Exception:
        pass
    sched = start_scheduler()
    yield
    try:
        sched.shutdown(wait=False)
    except Exception:
        pass


app = FastAPI(title="Hora da Tarefa", version="1.1.0", lifespan=lifespan,
              # A6: mapa da API só fora de prod
              docs_url=None if os.environ.get("ENV") == "prod" else "/docs",
              redoc_url=None if os.environ.get("ENV") == "prod" else "/redoc")

# A1: atrás do Caddy, o IP real vem em X-Forwarded-For — sem isso, todos os
# rate limits por IP compartilham a mesma chave (anti-brute-force fictício).
# Só confia em proxies das redes privadas + loopback.
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1", "10.0.0.0/8",
                                                          "172.16.0.0/12", "192.168.0.0/16"])

_origins = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Unauthorized)
async def _unauthorized_handler(request, exc: Unauthorized):
    return JSONResponse(status_code=401, content={
        "error": {"code": "UNAUTHORIZED", "message": str(exc) or "Não autenticado.", "details": {}}})


@app.exception_handler(RateLimited)
async def _ratelimited_handler(request, exc: RateLimited):
    return JSONResponse(status_code=429, content={
        "error": {"code": "RATE_LIMITED", "message": "Limite de requisições excedido.", "details": {}}},
        headers={"Retry-After": str(exc.retry_after)})


@app.exception_handler(PermissionError)
async def _forbidden_handler(request, exc: PermissionError):
    return JSONResponse(status_code=403, content={
        "error": {"code": "FORBIDDEN", "message": "Acesso restrito a administradores.", "details": {}}})


@app.middleware("http")
async def _global_rate_limit(request, call_next):
    if request.url.path.startswith("/v1/"):
        from app.core.ratelimit import _user_sub

        per_min, window = global_limit()
        allowed, retry = get_limiter().hit(f"rl:global:{_user_sub(request)}", per_min, window)
        if not allowed:
            return JSONResponse(status_code=429, content={
                "error": {"code": "RATE_LIMITED", "message": "Limite de requisições excedido.",
                          "details": {}}},
                headers={"Retry-After": str(retry)})
    return await call_next(request)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(children_router)
app.include_router(homeworks_router)
app.include_router(suggestions_router)
app.include_router(telegram_router)
app.include_router(notifications_router)
app.include_router(usage_router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    return {"ok": True, "checks": {"api": "up"}}
