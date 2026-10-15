from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import admin, webhook
from .db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="WhatsApp JobBot", version="0.1.0", lifespan=lifespan)
app.include_router(webhook.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"ok": True}
