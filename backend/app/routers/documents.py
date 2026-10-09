"""Documents router.

Owns the five document endpoints the approved API spec commits to: upload,
library listing, single-document status, deletion and question answering.
US-001-1 built the data model, local-disk storage and the CRUD surface.
US-003-1 added extraction and chunking. US-012-1 made the local vector index
real. US-013-1 (this ticket) replaces the raw-chunk echo on
`POST /documents/{id}/ask` with `app.services.answer_gen.generate_answer` --
a grounded, injection-resistant answer (or the verbatim fallback) built from
`app.services.retrieval`'s document-scoped chunks, with doc_processor,
retrieval and answer_gen remaining separate, independently callable modules.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings, redact_secret
from app.database import get_db
from app.models import Chunk, Document
from app.schemas import (
    SUPPORTED_DOCUMENT_TYPES,
    AskRequest,
    AskResponse,
    DocumentCreateResponse,
    DocumentStatusResponse,
)
from app.services import answer_gen, doc_processor, storage
from app.services.answer_gen import AnswerGenerationError
from app.services.embedder import EmbeddingError

router = APIRouter(prefix="/documents", tags=["documents"])

_SUPPORTED_TYPES_MESSAGE = "Supported types are PDF, DOCX and TXT."


def _extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _to_status_response(document: Document, chunk_count: int) -> DocumentStatusResponse:
    return DocumentStatusResponse(
        id=document.id,
        file_name=document.file_name,
        file_type=document.file_type,
        status=document.status,
        error_message=document.error_message,
        chunk_count=chunk_count,
        created_at=document.created_at,
    )


@router.post(
    "",
    response_model=DocumentCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"description": "Unsupported, empty or oversized file"},
    },
)
async def upload_document(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentCreateResponse:
    """Validate, persist to disk, record and process a new document.

    Size and extension are both checked before a single byte is written to
    disk (AC-002): an unsupported extension is rejected without ever reading
    the body, and an oversized body is rejected before `storage.save_file`
    is called. Once the row and file exist, extraction, chunking and
    embedding run synchronously via `doc_processor.process_document`, which
    always leaves the row at `status="ready"` or `status="failed"` -- with
    its error message and an orphaned file cleaned up on failure -- before
    this handler returns.
    """
    name = file.filename or "upload"
    extension = _extension_of(name)
    if extension not in SUPPORTED_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{extension or 'unknown'}'. {_SUPPORTED_TYPES_MESSAGE}",
        )

    content = await file.read()
    max_bytes = int(settings.max_upload_mb * 1024 * 1024)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds the {settings.max_upload_mb}MB upload limit.",
        )

    document = Document(
        id=uuid.uuid4(),
        file_name=name,
        file_type=extension,
        status="processing",
    )
    storage.save_file(document.id, extension, content)

    db.add(document)
    db.commit()
    db.refresh(document)

    doc_processor.process_document(document, content, db, settings)
    db.refresh(document)

    return DocumentCreateResponse(
        id=document.id,
        file_name=document.file_name,
        file_type=document.file_type,
        status=document.status,
        created_at=document.created_at,
    )


@router.get("", response_model=list[DocumentStatusResponse])
async def list_documents(db: Annotated[Session, Depends(get_db)]) -> list[DocumentStatusResponse]:
    """List every document in the library (AC-003: an empty library is a
    normal 200, not an error)."""
    documents = db.query(Document).order_by(Document.created_at).all()
    return [
        _to_status_response(doc, db.query(Chunk).filter(Chunk.document_id == doc.id).count())
        for doc in documents
    ]


@router.get(
    "/{document_id}",
    response_model=DocumentStatusResponse,
    responses={404: {"description": "Document not found"}},
)
async def get_document(
    document_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]
) -> DocumentStatusResponse:
    """Get a single document's current status, error detail and chunk count."""
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    chunk_count = db.query(Chunk).filter(Chunk.document_id == document.id).count()
    return _to_status_response(document, chunk_count)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Document not found"}},
)
async def delete_document(document_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]) -> None:
    """Permanently remove a document, its chunks/embeddings and its file."""
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    storage.delete_file(document.id, document.file_type)
    db.delete(document)
    db.commit()


@router.post(
    "/{document_id}/ask",
    response_model=AskResponse,
    responses={
        400: {"description": "Empty or whitespace-only question"},
        404: {"description": "Document not found"},
        409: {"description": "Document is not ready"},
        503: {"description": "Retrieval or model failure"},
    },
)
async def ask_document(
    document_id: uuid.UUID,
    body: AskRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AskResponse:
    """Answer a question scoped to one ready document.

    All retrieval and generation lives behind `app.services.answer_gen
    .generate_answer`, which in turn calls `app.services.retrieval.search`
    -- filtered by `document_id` at the query level (AC-035/AC-045), so a
    chunk from any other document can never come back -- and grounds the
    answer in only what was retrieved, returning the verbatim fallback
    reply whenever the context does not answer the question (including when
    retrieval is too weak to trust, AC-044). Embedding or model failure
    never leaks provider text to the client: both `EmbeddingError` and
    `AnswerGenerationError` carry an already human-readable message, passed
    through `redact_secret` before it becomes the 503 detail.
    """
    if not body.question.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question is empty")

    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is not ready yet. Wait for processing to finish.",
        )

    try:
        result = answer_gen.generate_answer(db, document, body.question, settings)
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=redact_secret(exc.message, settings),
        ) from exc
    except AnswerGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=redact_secret(exc.message, settings),
        ) from exc

    return AskResponse(
        answer=result.answer,
        source=result.source,
        is_fallback=result.is_fallback,
        model_configured=settings.llm_configured,
    )
