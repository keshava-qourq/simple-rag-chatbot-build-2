"""Local-disk storage for uploaded document bytes.

AC-069: documents, chunks and embeddings live on local disk / the local
database only -- no object store, no per-user or per-tenant separation, no
sharing. Each file is named deterministically from the document's id and
extension, so the path never has to be persisted as its own column.

The default storage directory is a fixed, documented path on local disk
(`backend/data/documents`) -- resolved from this module's own location, not
the process's current working directory -- so uploaded files are found in
the same place after a process stop and restart (AC-009-1). It is never
inside a temp directory, and is created on first use.
"""

import os
import uuid
from pathlib import Path

# backend/app/services/storage.py -> backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_STORAGE_DIR = _BACKEND_DIR / "data" / "documents"

STORAGE_DIR = Path(os.getenv("DOCUMENT_STORAGE_DIR", str(_DEFAULT_STORAGE_DIR)))


def _ensure_dir() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def file_path_for(document_id: uuid.UUID, file_type: str) -> Path:
    """The deterministic on-disk path for a document's stored file."""
    return STORAGE_DIR / f"{document_id}.{file_type}"


def save_file(document_id: uuid.UUID, file_type: str, content: bytes) -> Path:
    """Write validated bytes to disk. Called only after every check passes."""
    _ensure_dir()
    path = file_path_for(document_id, file_type)
    path.write_bytes(content)
    return path


def delete_file(document_id: uuid.UUID, file_type: str) -> None:
    """Remove the stored file, if present. Silent when already gone."""
    file_path_for(document_id, file_type).unlink(missing_ok=True)
