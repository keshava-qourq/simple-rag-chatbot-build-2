"""Pydantic request and response models.

One schema per body the approved API spec commits to, so every router
imports a name that already matches the committed contract instead of each
ticket inventing its own shape. Where a handler is still a stub, the shape is
the contract that is fixed now; the value returned is a placeholder until the
handler behind it is implemented.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# The architecture's three accepted upload formats (doc_processor).
SUPPORTED_DOCUMENT_TYPES: list[str] = ["pdf", "docx", "txt"]


class ErrorResponse(BaseModel):
    """The `{error}` body every 4xx/5xx in the API spec returns."""

    error: str


class DocumentCreateResponse(BaseModel):
    """201/202 body for `POST /documents`."""

    id: uuid.UUID
    file_name: str
    file_type: str
    status: str
    created_at: datetime


class DocumentStatusResponse(BaseModel):
    """Body for `GET /documents` (as a list) and `GET /documents/{id}`."""

    id: uuid.UUID
    file_name: str
    file_type: str
    status: str
    error_message: str | None = None
    chunk_count: int
    created_at: datetime


class AskRequest(BaseModel):
    """Body for `POST /documents/{id}/ask`."""

    question: str


class SourceReference(BaseModel):
    """The `source` object attached to a grounded answer.

    Exactly one of `page_number` (PDF) or `chunk_index` (DOCX/TXT) is set,
    matching the chunk the answer was grounded in.
    """

    document_name: str
    page_number: int | None = None
    chunk_index: int | None = None


class AskResponse(BaseModel):
    """200 body for `POST /documents/{id}/ask`."""

    # The spec's field is literally `model_configured`; pydantic only warns
    # about its default "model_" protected namespace here, it does not
    # reject it. Disabling the check is correct, not a workaround.
    model_config = ConfigDict(protected_namespaces=())

    answer: str
    source: SourceReference | None = None
    is_fallback: bool
    model_configured: bool


class ConfigStatusResponse(BaseModel):
    """200 body for `GET /config/status`.

    Reports presence and model *names* only -- never a key or key fragment.
    """

    model_config = ConfigDict(protected_namespaces=())

    llm_configured: bool
    embedding_configured: bool
    llm_model: str | None = None
    embedding_model: str | None = None
    issues: list[str] = Field(default_factory=list)
