"""Tests for US-008-1: `DELETE /documents/{id}` cascade and permanence.

Covers AC-024 (the row, every chunk/embedding and the on-disk file are all
removed; a subsequent `GET /documents` omits it and `GET /documents/{id}`
returns 404) and AC-025 (retrieval for any remaining document can never
return a chunk originating from the deleted document, and no undo/restore/
archive endpoint exists). Run after US-012-1 so the retrieval assertion is
meaningful; the embedding provider is stubbed via
`monkeypatch.setattr(retrieval, "embed_texts", ...)`, never reaching the
network.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.config import Settings
from app.database import Base, get_db
from app.main import app
from app.models import Chunk, Document
from app.services import retrieval, storage

TEST_DB_URL = "sqlite:///./test_document_deletion.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _override_get_db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_schema_and_overrides(tmp_path, monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path / "documents")

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    yield
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def _make_ready_document(db, file_name: str = "notes.txt") -> Document:
    document = Document(id=uuid.uuid4(), file_name=file_name, file_type="txt", status="ready")
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _add_chunk(db, document_id, chunk_index, content, embedding) -> Chunk:
    chunk = Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        embedding=embedding,
    )
    db.add(chunk)
    db.commit()
    return chunk


def test_delete_removes_every_chunk_and_embedding_and_the_on_disk_file() -> None:
    """AC-024: deleting a document removes its row, every chunk/embedding
    row, and its on-disk file; a subsequent GET of either the single
    document or the library omits it."""
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "first chunk", [1.0, 0.0])
    _add_chunk(db, document.id, 1, "second chunk", [0.0, 1.0])
    storage.save_file(document.id, "txt", b"hello world")
    stored_path = storage.file_path_for(document.id, "txt")
    assert stored_path.exists()
    doc_id = document.id
    db.close()

    response = client.delete(f"/documents/{doc_id}")
    assert response.status_code == 204

    assert not stored_path.exists()

    db = TestingSessionLocal()
    assert db.get(Document, doc_id) is None
    assert db.query(Chunk).filter(Chunk.document_id == doc_id).count() == 0
    db.close()

    assert client.get(f"/documents/{doc_id}").status_code == 404
    listing = client.get("/documents")
    assert listing.status_code == 200
    assert all(d["id"] != str(doc_id) for d in listing.json())


def test_delete_unknown_document_returns_404() -> None:
    response = client.delete(f"/documents/{uuid.uuid4()}")
    assert response.status_code == 404


def test_retrieval_for_remaining_document_never_returns_a_chunk_from_the_deleted_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-025: after deletion, a search scoped to any remaining document can
    never surface a chunk that originated from the deleted document --
    their rows are gone outright, not merely filtered out at query time,
    even when their embeddings are identical to the surviving chunk's."""
    db = TestingSessionLocal()
    kept = _make_ready_document(db, "kept.txt")
    doomed = _make_ready_document(db, "doomed.txt")
    _add_chunk(db, kept.id, 0, "kept chunk", [1.0, 0.0])
    _add_chunk(db, doomed.id, 0, "doomed chunk, identical embedding", [1.0, 0.0])
    doomed_id = doomed.id
    db.close()

    delete_response = client.delete(f"/documents/{doomed_id}")
    assert delete_response.status_code == 204

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    settings = Settings(embedding_api_key="test-key", retrieval_top_k=5)

    db = TestingSessionLocal()
    results = retrieval.search(db, kept.id, "anything", settings)
    db.close()

    assert len(results) == 1
    assert results[0].document_id == kept.id
    assert results[0].content == "kept chunk"

    db = TestingSessionLocal()
    assert db.query(Chunk).filter(Chunk.document_id == doomed_id).count() == 0
    db.close()


def test_no_undo_restore_or_archive_endpoint_exists() -> None:
    """AC-025: deletion is permanent by design -- no endpoint exists to
    undo, restore or archive a deleted document."""
    response = client.get("/openapi.json")
    paths = response.json()["paths"]

    forbidden_terms = ("restore", "undo", "archive", "undelete", "trash", "recycle")
    for path in paths:
        lowered = path.lower()
        assert not any(term in lowered for term in forbidden_terms), path
