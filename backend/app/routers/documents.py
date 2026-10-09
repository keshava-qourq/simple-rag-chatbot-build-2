"""Documents router.

Owns the five document endpoints the approved API spec commits to: upload,
library listing, single-document status, deletion and question answering.
This ticket (US-001-1) implements the data model, local-disk storage and the
CRUD surface -- upload, list, get-one and delete. Extraction, chunking and
embedding are US-002-1's job: a freshly uploaded document is persisted with
status "processing" and left there.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Chunk, Document
from app.schemas import (
    SUPPORTED_DOCUMENT_TYPES,
    AskRequest,
    AskResponse,
    DocumentCreateResponse,
    DocumentStatusResponse,
)
from app.services import storage

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
    """Validate, persist to disk and record a new document.

    Size and extension are both checked before a single byte is written to
    disk (AC-002): an unsupported extension is rejected without ever reading
    the body, and an oversized body is rejected before `storage.save_file`
    is called.
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
        409: {"description": "Document is not ready"},
        503: {"description": "Retrieval or model failure"},
    },
)
async def ask_document(document_id: uuid.UUID, body: AskRequest) -> AskResponse:
    """Answer a question scoped to one ready document.

    Stub: retrieval restricted to this document and grounded generation are
    the approved `retrieval` and `answer_gen` functions' job, not implemented
    here yet. The one real check kept here is the spec's own 400 for an
    empty or whitespace-only question, since that needs no component at all.
    """
    if not body.question.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question is empty")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Retrieval and answer generation are not implemented yet.",
    )
