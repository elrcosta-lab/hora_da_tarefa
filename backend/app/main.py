"""FastAPI app (SPECS §3.1 v1.1)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.children import router as children_router
from app.api.homeworks import router as homeworks_router
from app.api.notifications import router as notifications_router
from app.api.suggestions import router as suggestions_router
from app.api.telegram import router as telegram_router


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
