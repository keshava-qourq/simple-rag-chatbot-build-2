"""Extra grounding-wiring tests for US-013-1, QA pass on US-013-3.

`test_answer_gen.py` already proves the fallback, source-shape, scoping and
injection-resistance contracts. This file adds the one thing those tests
leave implicit: that the *exact* retrieved chunk text -- not a summary, not
a placeholder, not fabricated content -- is what actually reaches
`_call_llm`, and that only chunks `retrieval.search` returned for *this*
document are ever passed to it (AC-037).
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.config import Settings
from app.database import Base
from app.models import Chunk, Document
from app.services import answer_gen, retrieval, storage

TEST_DB_URL = "sqlite:///./test_answer_gen_grounding.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _fresh_schema(tmp_path, monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path / "documents")
    yield
    Base.metadata.drop_all(bind=engine)


def _make_ready_document(db, file_name: str = "notes.txt", file_type: str = "txt") -> Document:
    document = Document(id=uuid.uuid4(), file_name=file_name, file_type=file_type, status="ready")
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _add_chunk(db, document_id, chunk_index, content, embedding, page_number=None) -> Chunk:
    chunk = Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        chunk_index=chunk_index,
        page_number=page_number,
        content=content,
        embedding=embedding,
    )
    db.add(chunk)
    db.commit()
    return chunk


def test_call_llm_receives_the_verbatim_retrieved_chunk_text_and_nothing_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-037: the generator is handed exactly the stored chunk content --
    proving an answer built from it cannot contain a fact absent from the
    stubbed retrieved chunks, because no other text ever reaches the model."""
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    stored_text = "The warranty period is 24 months from the date of purchase."
    _add_chunk(db, document.id, 0, stored_text, [1.0, 0.0])

    captured: dict = {}

    def _fake_call_llm(question, chunks, settings):
        captured["question"] = question
        captured["chunk_texts"] = [c.content for c in chunks]
        return "The warranty period is 24 months."

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    monkeypatch.setattr(answer_gen, "_call_llm", _fake_call_llm)
    settings = Settings(llm_api_key="test-llm-key", embedding_api_key="test-embed-key")

    result = answer_gen.generate_answer(db, document, "How long is the warranty?", settings)

    assert captured["chunk_texts"] == [stored_text]
    assert captured["question"] == "How long is the warranty?"
    assert result.answer == "The warranty period is 24 months."
    assert result.is_fallback is False
    db.close()


def test_call_llm_only_ever_receives_chunks_retrieval_search_actually_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The set of chunks passed to the generator is exactly
    `retrieval.search`'s return value -- not the full chunk table, and
    never a chunk belonging to a different document (AC-045)."""
    db = TestingSessionLocal()
    doc_a = _make_ready_document(db, "a.txt")
    doc_b = _make_ready_document(db, "b.txt")
    _add_chunk(db, doc_a.id, 0, "a-document relevant content", [1.0, 0.0])
    _add_chunk(db, doc_a.id, 1, "a-document irrelevant filler", [0.0, 1.0])
    _add_chunk(db, doc_b.id, 0, "b-document content, same embedding", [1.0, 0.0])

    captured_chunk_ids: list = []

    def _fake_call_llm(question, chunks, settings):
        captured_chunk_ids.extend(c.document_id for c in chunks)
        return "An answer grounded only in document a."

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    monkeypatch.setattr(answer_gen, "_call_llm", _fake_call_llm)
    settings = Settings(llm_api_key="test-llm-key", embedding_api_key="test-embed-key")

    answer_gen.generate_answer(db, doc_a, "what is relevant?", settings)

    assert captured_chunk_ids, "the generator must have been called with some chunks"
    assert all(doc_id == doc_a.id for doc_id in captured_chunk_ids)
    db.close()
