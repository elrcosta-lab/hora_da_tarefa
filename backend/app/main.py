"""FastAPI app (SPECS §3.1 v1.1)."""
from fastapi import FastAPI

from app.api.homeworks import router as homeworks_router

app = FastAPI(title="Hora da Tarefa", version="1.1.0")
app.include_router(homeworks_router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    return {"ok": True, "checks": {"api": "up"}}
