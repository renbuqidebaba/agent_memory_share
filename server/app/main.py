"""Agent Memory Server 入口。

启动：cd server && uvicorn app.main:app --host 127.0.0.1 --port 8787
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import init_db
from .routers import memory, messages, summary


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Agent Memory Server", version="0.1.0", lifespan=lifespan)
app.include_router(memory.router)
app.include_router(messages.router)
app.include_router(summary.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
