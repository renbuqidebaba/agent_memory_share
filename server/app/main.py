"""Agent Handoff HTTP server; legacy memory endpoints remain available.

启动：cd server && uvicorn app.main:app --host 127.0.0.1 --port 8787
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .db import init_db
from .routers import handoff, memory, messages, summary


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Agent Handoff", version=__version__, lifespan=lifespan)
app.include_router(memory.router)
app.include_router(messages.router)
app.include_router(summary.router)
app.include_router(handoff.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}
