"""Application entrypoint.

Generated from the approved architecture: one router per component that owns
endpoints, one route per endpoint the API spec declares. Most generated
routes are stubs that return a typed placeholder, so the service starts,
serves its OpenAPI document and passes its tests before a single handler's
real behaviour is implemented.
"""

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app import models  # noqa: F401 -- imported so the tables register before create_all
from app.config import get_settings
from app.database import Base, engine
from app.routers import config as config_router
from app.routers import documents

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Simple RAG Chatbot — Build (2)",
    description="Simple RAG Chatbot — Build Instructions",
    version="0.1.0",
)

# The SPA runs on a different origin than the API, so the browser refuses its calls
# unless that origin is allowed here. In development that is the Vite dev server; when
# deployed, the platform injects the frontend's real URL as ALLOWED_ORIGINS (comma
# separated). Point ALLOWED_ORIGINS at the real thing and nothing else has to change.
_dev_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins or _dev_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The scaffold ships no migrations, so the tables are created from the models on
# startup. Replace this with Alembic before anything holds data worth keeping.
Base.metadata.create_all(bind=engine)

# Validate configuration once, at import time, so a malformed `.env` value
# (AC-063) is reported here -- by field name, with no stack trace reaching a
# user -- instead of surfacing later as an opaque failure on the first
# request that happens to touch `get_settings()`. A missing API key is not an
# error here: the architecture requires the app to start cleanly with none
# set, so only `ValidationError` (type/range/format problems) is caught.
try:
    get_settings()
except ValidationError as exc:
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"]).upper()
        logger.error("Invalid configuration for %s: %s", field, error["msg"])

app.include_router(documents.router)
app.include_router(config_router.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe, and the only route here that is not a stub."""
    return {"status": "ok"}
