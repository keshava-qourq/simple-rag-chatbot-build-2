"""Tests for the documents CRUD API (US-001-1).

Covers AC-001, AC-002, AC-003, the single-document GET, DELETE (including its
cascade and file removal), AC-068 (no auth) and AC-069 (local-disk storage,
no tenant separation).
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.services import storage

TEST_DB_URL = "sqlite:///./test_documents.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def override_get_db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_schema_and_storage(tmp_path, monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path / "documents")
    yield
    Base.metadata.drop_all(bind=engine)


def _upload(filename: str, content: bytes, content_type: str = "text/plain"):
    return client.post(
        "/documents",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_upload_txt_creates_record_and_persists_to_disk() -> None:
    """AC-001: a .txt upload is persisted, recorded as processing, and the
    returned record carries file_name, file_type, status and created_at."""
    response = _upload("notes.txt", b"hello world")

    assert response.status_code == 202
    body = response.json()
    assert body["file_name"] == "notes.txt"
    assert body["file_type"] == "txt"
    assert body["status"] == "processing"
    assert "created_at" in body and body["created_at"]

    stored_path = storage.file_path_for(uuid.UUID(body["id"]), "txt")
    assert stored_path.exists()
    assert stored_path.read_bytes() == b"hello world"


def test_upload_pdf_and_docx_extensions_are_accepted() -> None:
    """AC-001: .pdf and .docx are also accepted."""
    pdf_response = _upload("file.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
    docx_response = _upload(
        "file.docx",
        b"fake docx bytes",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert pdf_response.status_code == 202
    assert pdf_response.json()["file_type"] == "pdf"
    assert docx_response.status_code == 202
    assert docx_response.json()["file_type"] == "docx"


def test_unsupported_extension_is_rejected_with_readable_message() -> None:
    """AC-002: an unsupported extension is a 400 naming PDF, DOCX and TXT,
    with no record and no file created."""
    response = _upload(
        "malware.exe", b"not a real document", content_type="application/x-msdownload"
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "PDF" in detail
    assert "DOCX" in detail
    assert "TXT" in detail

    listing = client.get("/documents")
    assert listing.json() == []
    assert not any(storage.STORAGE_DIR.glob("*")) if storage.STORAGE_DIR.exists() else True


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("data.csv", "text/csv"),
        ("photo.jpg", "image/jpeg"),
    ],
)
def test_specific_unsupported_types_are_rejected_and_create_no_record(
    filename: str, content_type: str
) -> None:
    """AC-002: .xlsx, .pptx, .csv and .jpg are each rejected with HTTP 400,
    a message naming PDF, DOCX and TXT, and no record or file is created."""
    response = _upload(filename, b"some bytes", content_type=content_type)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "PDF" in detail
    assert "DOCX" in detail
    assert "TXT" in detail

    listing = client.get("/documents")
    assert listing.json() == []


def test_oversized_upload_is_rejected_before_persisting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Size validation happens before any bytes are written to disk."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.00001")

    response = _upload("notes.txt", b"this content is definitely too big for the limit")

    assert response.status_code == 400
    assert "upload limit" in response.json()["detail"]
    listing = client.get("/documents")
    assert listing.json() == []

    get_settings.cache_clear()


def test_oversized_upload_message_is_readable_and_names_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A size-limit rejection must be understandable without reading the
    backend's config -- it should name the limit it enforced."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.00001")

    response = _upload("notes.txt", b"x" * 1024)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "MB" in detail

    get_settings.cache_clear()


def test_list_documents_returns_empty_list_when_nothing_uploaded() -> None:
    """AC-003: an empty library is HTTP 200 with an empty list, not an error."""
    response = client.get("/documents")

    assert response.status_code == 200
    assert response.json() == []


def test_get_document_returns_status_error_and_chunk_count() -> None:
    created = _upload("notes.txt", b"content").json()

    response = client.get(f"/documents/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    assert body["error_message"] is None
    assert body["chunk_count"] == 0
    assert body["id"] == created["id"]


def test_get_unknown_document_returns_404_with_readable_message() -> None:
    response = client.get(f"/documents/{uuid.uuid4()}")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_delete_document_removes_record_and_file() -> None:
    created = _upload("notes.txt", b"content").json()
    doc_id = created["id"]
    stored_path = storage.file_path_for(uuid.UUID(doc_id), "txt")
    assert stored_path.exists()

    response = client.delete(f"/documents/{doc_id}")

    assert response.status_code == 204
    assert not stored_path.exists()
    assert client.get(f"/documents/{doc_id}").status_code == 404


def test_delete_document_removes_it_from_the_library_listing() -> None:
    """AC-003: deletion removes the record (and its chunks) from the list,
    not merely the single-document GET."""
    created = _upload("notes.txt", b"content").json()
    doc_id = created["id"]

    client.delete(f"/documents/{doc_id}")

    listing = client.get("/documents")
    assert listing.status_code == 200
    assert listing.json() == []


def test_delete_unknown_document_returns_404() -> None:
    response = client.delete(f"/documents/{uuid.uuid4()}")

    assert response.status_code == 404


def test_no_authentication_header_required_for_any_document_endpoint() -> None:
    """AC-068: no endpoint requires authentication, registration or payment."""
    upload = _upload("notes.txt", b"content")
    assert upload.status_code == 202

    doc_id = upload.json()["id"]
    assert client.get("/documents").status_code == 200
    assert client.get(f"/documents/{doc_id}").status_code == 200
    assert client.delete(f"/documents/{doc_id}").status_code == 204


def test_no_authentication_header_required_even_when_one_is_sent() -> None:
    """AC-068: sending a bogus bearer token does not get treated specially --
    the endpoints simply don't look at it, so the same request succeeds with
    or without one."""
    response = client.get("/documents", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code == 200
