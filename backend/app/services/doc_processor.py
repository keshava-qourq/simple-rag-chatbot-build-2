"""Document extraction, chunking and embedding pipeline (US-003-1, US-003-2).

Runs synchronously inside `POST /documents` -- this scaffold has no
background job queue, so by the time the handler returns, a document has
already settled at `status="ready"` or `status="failed"`. It is never left
"processing" past the end of the request, and no half-processed document (a
row with no chunks, or a partial set of chunks, or chunks with no embedding)
is ever visible to a caller.

Chosen failure rule (documented here because AC-014 allows either "removed"
or "marked failed"): a failed upload's `documents` row is **kept**, not
deleted, with `status="failed"` and a human-readable `error_message` -- so a
failed attempt stays visible on `GET /documents` and `GET /documents/{id}`
instead of vanishing silently. Its on-disk file is always removed, though: a
failed document never has a reachable file behind it. Any chunks that might
have been written before the failure was detected -- extraction, chunking,
*or* embedding -- are rolled back first, so a document is always either
fully chunked-and-embedded or has zero chunk rows, never a partial set
(AC-010).

Every error message here is deliberately written for a human reading
`GET /documents/{id}`, never a parser's or provider's raw exception text or
traceback -- AC-015 and AC-016 both specifically forbid surfacing that. Any
exception text that is logged for debugging is passed through
`app.config.redact_secret` first, on the off chance a provider/parser error
happens to echo a configured key back; `embedder.embed_texts` already does
this itself for every embedding-provider failure (AC: "secrets are never
present in error_message or logs").
"""

import logging
import uuid
from dataclasses import dataclass
from io import BytesIO

from sqlalchemy.orm import Session

from app.config import Settings, redact_secret
from app.models import Chunk, Document
from app.services import embedder, storage

logger = logging.getLogger(__name__)

EMPTY_FILE_MESSAGE = (
    "This file is empty and contains no readable text. Upload a file that has content."
)
CORRUPT_FILE_MESSAGE = (
    "This file could not be read or appears damaged. Re-export or re-save it and "
    "try uploading again."
)
SCANNED_PDF_MESSAGE = (
    "No text could be extracted from this PDF. Scanned or image-only PDFs are "
    "unsupported because OCR is out of scope -- upload a PDF with a text layer instead."
)


class DocumentProcessingError(Exception):
    """Carries only the human-readable message a caller is allowed to see."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ExtractedPage:
    page_number: int | None
    text: str


def _extract_txt(content: bytes) -> list[ExtractedPage]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1", errors="replace")
    return [ExtractedPage(page_number=None, text=text)]


def _extract_pdf(content: bytes) -> list[ExtractedPage]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        pages: list[ExtractedPage] = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(ExtractedPage(page_number=index, text=text))
        return pages
    except Exception as exc:
        logger.warning("PDF extraction failed: %s", redact_secret(str(exc)))
        raise DocumentProcessingError(CORRUPT_FILE_MESSAGE) from exc


def _extract_docx(content: bytes) -> list[ExtractedPage]:
    try:
        import docx

        document = docx.Document(BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return [ExtractedPage(page_number=None, text=text)]
    except Exception as exc:
        logger.warning("DOCX extraction failed: %s", redact_secret(str(exc)))
        raise DocumentProcessingError(CORRUPT_FILE_MESSAGE) from exc


_EXTRACTORS = {
    "txt": _extract_txt,
    "pdf": _extract_pdf,
    "docx": _extract_docx,
}


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Fixed-size character chunks with overlap, sized from `Settings`."""
    length = len(text)
    if length == 0:
        return []
    step = max(chunk_size - chunk_overlap, 1)
    chunks: list[str] = []
    start = 0
    while start < length:
        end = min(start + chunk_size, length)
        piece = text[start:end]
        if piece.strip():
            chunks.append(piece)
        if end >= length:
            break
        start += step
    return chunks


def _build_chunks(pages: list[ExtractedPage], settings: Settings) -> list[tuple[str, int | None]]:
    """Returns `(content, page_number)` tuples, in document order."""
    chunks: list[tuple[str, int | None]] = []
    for page in pages:
        for piece in _chunk_text(page.text, settings.chunk_size, settings.chunk_overlap):
            chunks.append((piece, page.page_number))
    return chunks


def _mark_failed(document: Document, message: str, db: Session, settings: Settings) -> None:
    db.rollback()
    document.status = "failed"
    document.error_message = redact_secret(message, settings)
    db.add(document)
    db.commit()
    storage.delete_file(document.id, document.file_type)


def process_document(
    document: Document,
    content: bytes,
    db: Session,
    settings: Settings,
) -> None:
    """Extract, chunk, embed and persist, or mark the document failed.

    Always leaves `document` at `status="ready"` or `status="failed"`, with
    `db` committed, before returning -- never partway, and never still
    "processing" once the call finishes. A chunk row is only ever written
    with its embedding already computed (AC-008): embedding happens before
    any `Chunk` is added to the session, so a failure there (including no
    embedding model configured at all) leaves zero chunk rows, not partial
    ones (AC-010).
    """
    try:
        if not content or not content.strip():
            raise DocumentProcessingError(EMPTY_FILE_MESSAGE)

        extractor = _EXTRACTORS.get(document.file_type)
        if extractor is None:  # pragma: no cover - router already rejects this
            raise DocumentProcessingError(CORRUPT_FILE_MESSAGE)

        pages = extractor(content)
        chunk_tuples = _build_chunks(pages, settings)

        if not chunk_tuples:
            if document.file_type == "pdf":
                raise DocumentProcessingError(SCANNED_PDF_MESSAGE)
            raise DocumentProcessingError(EMPTY_FILE_MESSAGE)

        texts = [text for text, _ in chunk_tuples]
        try:
            vectors = embedder.embed_texts(texts, settings)
        except embedder.EmbeddingError as exc:
            raise DocumentProcessingError(exc.message) from exc

        for index, ((text, page_number), vector) in enumerate(
            zip(chunk_tuples, vectors, strict=True)
        ):
            db.add(
                Chunk(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    chunk_index=index,
                    page_number=page_number,
                    content=text,
                    embedding=vector,
                )
            )
        document.status = "ready"
        document.error_message = None
        db.commit()
    except DocumentProcessingError as exc:
        _mark_failed(document, exc.message, db, settings)
    except Exception:  # pragma: no cover - unexpected failure safety net
        logger.exception("Unexpected failure processing document %s", document.id)
        _mark_failed(
            document,
            "This file could not be processed due to an unexpected error.",
            db,
            settings,
        )
