"""Tests for the embedding prerequisite of US-012-1: `doc_processor` now
embeds the chunks `build_chunks` produces, writes `Chunk` rows carrying
those embeddings, and only then promotes the document to `status="ready"`.
A document is never visible with a partial or unembedded chunk set; an
embedding failure leaves it `status="failed"` with a readable
`error_message`.
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.config import Settings
from app.database import Base
from app.models import Chunk, Document
from app.services import doc_processor, storage
from app.services.embedder import EmbeddingError

TEST_DB_URL = "sqlite:///./test_doc_processor_embedding.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _fresh_schema_and_storage(tmp_path, monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path / "documents")
    yield
    Base.metadata.drop_all(bind=engine)


def _settings() -> Settings:
    return Settings(
        llm_api_key=None,
        embedding_api_key="test-key",
        chunk_size=20,
        chunk_overlap=0,
        retrieval_top_k=3,
        max_upload_mb=20.0,
    )


def test_process_document_embeds_chunks_and_promotes_to_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = TestingSessionLocal()
    document = Document(
        id=uuid.uuid4(), file_name="notes.txt", file_type="txt", status="processing"
    )
    content = b"hello world, this is a test document with content for chunking"
    storage.save_file(document.id, "txt", content)
    db.add(document)
    db.commit()

    settings = _settings()

    def fake_embed_texts(texts, _settings):
        return [[float(i), 0.0] for i, _ in enumerate(texts)]

    monkeypatch.setattr(doc_processor, "embed_texts", fake_embed_texts)

    doc_processor.process_document(document, content, db, settings)
    db.refresh(document)

    assert document.status == "ready"
    assert document.error_message is None

    chunks = (
        db.query(Chunk).filter(Chunk.document_id == document.id).order_by(Chunk.chunk_index).all()
    )
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.embedding is not None
        assert len(chunk.embedding) == 2

    db.close()


def test_process_document_embedding_failure_marks_document_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = TestingSessionLocal()
    document = Document(
        id=uuid.uuid4(), file_name="notes.txt", file_type="txt", status="processing"
    )
    content = b"hello world, this is a test document with content for chunking"
    storage.save_file(document.id, "txt", content)
    db.add(document)
    db.commit()

    settings = _settings()

    def fake_embed_texts(texts, _settings):
        raise EmbeddingError("The embedding provider rejected the configured API key.")

    monkeypatch.setattr(doc_processor, "embed_texts", fake_embed_texts)

    doc_processor.process_document(document, content, db, settings)
    db.refresh(document)

    assert document.status == "failed"
    assert document.error_message
    assert "rejected" in document.error_message

    chunks = db.query(Chunk).filter(Chunk.document_id == document.id).all()
    assert chunks == []

    stored_path = storage.file_path_for(document.id, "txt")
    assert not stored_path.exists()

    db.close()
