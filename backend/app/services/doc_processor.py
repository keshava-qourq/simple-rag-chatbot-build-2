"""Document extraction and chunking (US-003-1).

Runs synchronously inside `POST /documents` -- this scaffold has no
background job queue, so by the time the handler returns, a document has
already been fully validated: either its text extracted and split into
chunks without error, or it has settled at `status="failed"` with a
human-readable `error_message` and its on-disk file removed. It is never
left "processing" after a *failure*; the on-disk file is always removed in
that case too.

Embedding the chunks, writing `Chunk` rows and promoting the document to
`status="ready"` are **US-003-2's job**, not this one's -- this ticket is
scoped to extraction and chunking only (see the ticket's own constraint:
"Do not touch embedding, the vector index or POST /documents/{id}/ask").
Accordingly, a document that extracts and chunks cleanly is left at
`status="processing"` (its status at creation) with zero `Chunk` rows: a
`Chunk` is only ever written once it has an embedding (AC-008 in US-003-2),
so no `Chunk` row is written here at all. A document is only ever visible
with chunks once US-003-2 has embedded every one of them -- never with a
partial set, and never with one that has no embedding (AC-010).

Chosen failure rule (documented here because AC-014 allows either "removed"
or "marked failed"): a failed upload's `documents` row is **kept**, not
deleted, with `status="failed"` and a human-readable `error_message` -- so a
failed attempt stays visible on `GET /documents` and `GET /documents/{id}`
instead of vanishing silently. Its on-disk file is always removed, though: a
failed document never has a reachable file behind it.

Every error message here is deliberately written for a human reading
`GET /documents/{id}`, never a parser's raw exception text or traceback --
AC-015 and AC-016 both specifically forbid surfacing that. Any exception
text that is logged for debugging is passed through `app.config.redact_secret`
first, on the off chance a parser error happens to echo a configured key
back.
"""

import logging
from dataclasses import dataclass
from io import BytesIO

from sqlalchemy.orm import Session

from app.config import Settings, redact_secret
from app.models import Document
from app.services import storage

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


def build_chunks(pages: list[ExtractedPage], settings: Settings) -> list[tuple[str, int | None]]:
    """Returns `(content, page_number)` tuples, in document order, each
    carrying its future `chunk_index` as its position in this list. Exposed
    (not prefixed `_`) so US-003-2's embedding stage can reuse it rather than
    re-split the text itself.
    """
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
    """Extract and chunk, or mark the document failed.

    Validates that the upload has readable text and can be split into at
    least one non-empty chunk, surfacing only the human-readable taxonomy of
    errors (AC-014/015/016) and never a raw parser exception. On success the
    document is left exactly as it was handed in (`status="processing"`,
    no chunk rows) for US-003-2 to pick up and embed; on any failure it is
    left at `status="failed"` with a readable `error_message`, its on-disk
    file removed, and `db` already committed -- never still "processing"
    once this call returns.
    """
    try:
        if not content or not content.strip():
            raise DocumentProcessingError(EMPTY_FILE_MESSAGE)

        extractor = _EXTRACTORS.get(document.file_type)
        if extractor is None:  # pragma: no cover - router already rejects this
            raise DocumentProcessingError(CORRUPT_FILE_MESSAGE)

        pages = extractor(content)
        chunk_tuples = build_chunks(pages, settings)

        if not chunk_tuples:
            if document.file_type == "pdf":
                raise DocumentProcessingError(SCANNED_PDF_MESSAGE)
            raise DocumentProcessingError(EMPTY_FILE_MESSAGE)

        # Extraction and chunking succeeded. Chunk rows and the "ready"
        # status are written only once US-003-2 embeds every chunk; nothing
        # further happens here.
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
