"""Documents router.

Owns the five document endpoints the approved API spec commits to: upload,
library listing, single-document status, deletion and question answering.
Each handler below is routed, typed and documented in the OpenAPI schema the
frontend is built against -- but the behaviour behind it (`doc_processor`,
`retrieval`, `answer_gen`, and the datastore queries that back them) is the
development sprint's work, implemented ticket by ticket, not this scaffold's.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.schemas import (
    AskRequest,
    AskResponse,
    DocumentCreateResponse,
    DocumentStatusResponse,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"description": "Unsupported, empty or corrupt file"},
        415: {"description": "Unsupported file type"},
    },
)
async def upload_document(file: Annotated[UploadFile, File()]) -> DocumentCreateResponse:
    """Accept a PDF, DOCX or TXT file and queue it for processing.

    Stub: type/size validation, per-format extraction, chunking, embedding
    and storage are the approved `doc_processor` function's job, and
    persisting the resulting document row is the datastore's -- neither is
    implemented here yet.
    """
    name = file.filename or "upload"
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return DocumentCreateResponse(
        id=uuid.uuid4(),
        file_name=name,
        file_type=suffix,
        status="processing",
    )


@router.get("", response_model=list[DocumentStatusResponse])
async def list_documents() -> list[DocumentStatusResponse]:
    """List every document in the library.

    Stub: an empty library is the correct response before the datastore is
    wired to this handler and any document has been persisted.
    """
    return []


@router.get(
    "/{document_id}",
    response_model=DocumentStatusResponse,
    responses={404: {"description": "Document not found"}},
)
async def get_document(document_id: uuid.UUID) -> DocumentStatusResponse:
    """Get a single document's current status and error detail.

    Stub: with no datastore query behind it yet, every id is correctly not
    found.
    """
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Document not found"}},
)
async def delete_document(document_id: uuid.UUID) -> None:
    """Permanently remove a document and cascade-delete its chunks and
    embeddings.

    Stub: the cascade itself is modelled at the schema level in
    `app.models.Chunk` (`ondelete="CASCADE"`); issuing the delete is the
    development sprint's work.
    """
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


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
