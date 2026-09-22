"""FastAPI app (SPECS §3.1 v1.1)."""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.children import router as children_router
from app.api.homeworks import router as homeworks_router
from app.api.notifications import router as notifications_router
from app.api.suggestions import router as suggestions_router
from app.api.telegram import router as telegram_router
from app.core.security import Unauthorized
from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.core.db import init_db
    from app.tasks.beat import start_scheduler

    init_db()
    sched = start_scheduler()
    yield
    try:
        sched.shutdown(wait=False)
    except Exception:
        pass


app = FastAPI(title="Hora da Tarefa", version="1.1.0", lifespan=lifespan)

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

app.include_router(auth_router)
app.include_router(children_router)
app.include_router(homeworks_router)
app.include_router(suggestions_router)
app.include_router(telegram_router)
app.include_router(notifications_router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    return {"ok": True, "checks": {"api": "up"}}
