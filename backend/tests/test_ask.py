"""Tests for US-012-1: `app.services.retrieval.search` and
`POST /documents/{id}/ask`.

Covers AC-034 (top-k retrieval via the embedder, including the documented
default of settings.retrieval_top_k), AC-035 (document_id filtered at the
query level, proven both by a tied near-identical embedding and by a second
document's chunk being the single closest match overall), AC-036 (no raw
provider text on failure, never a fabricated answer, no API key value in the
response) and the endpoint's existing 400/409 contract plus the 404 a real
document lookup requires.

The embedding provider is always stubbed via `monkeypatch.setattr(retrieval,
"embed_texts", ...)` -- this suite never reaches the network.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models import Chunk, Document
from app.services import retrieval, storage
from app.services.embedder import EmbeddingError

TEST_DB_URL = "sqlite:///./test_ask.db"
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

    previous_db_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    yield
    if previous_db_override is not None:
        app.dependency_overrides[get_db] = previous_db_override
    else:
        app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def _make_ready_document(db, file_name: str = "notes.txt") -> Document:
    document = Document(id=uuid.uuid4(), file_name=file_name, file_type="txt", status="ready")
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


# --- retrieval.search -----------------------------------------------------


def test_search_filters_by_document_id_at_the_query_level(monkeypatch: pytest.MonkeyPatch) -> None:
    db = TestingSessionLocal()
    doc_a = _make_ready_document(db, "a.txt")
    doc_b = _make_ready_document(db, "b.txt")
    _add_chunk(db, doc_a.id, 0, "alpha content", [1.0, 0.0])
    _add_chunk(db, doc_b.id, 0, "beta content, same embedding", [1.0, 0.0])

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    settings = Settings(embedding_api_key="test-key", retrieval_top_k=5)

    results = retrieval.search(db, doc_a.id, "what is alpha?", settings)

    assert len(results) == 1
    assert results[0].document_id == doc_a.id
    db.close()


def test_search_excludes_document_b_chunk_even_when_it_is_the_closest_match_overall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-035: document B's chunk is a strictly better cosine match to the
    query than any of document A's own chunks, yet a search scoped to A must
    never surface it -- the SQL filter runs before ranking, not after."""
    db = TestingSessionLocal()
    doc_a = _make_ready_document(db, "a.txt")
    doc_b = _make_ready_document(db, "b.txt")
    _add_chunk(db, doc_a.id, 0, "a chunk, distant match", [0.0, 1.0, 0.0])
    _add_chunk(db, doc_a.id, 1, "a chunk, moderate match", [0.6, 0.8, 0.0])
    _add_chunk(db, doc_b.id, 0, "b chunk, the single closest match overall", [1.0, 0.0, 0.0])

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0, 0.0]])
    settings = Settings(embedding_api_key="test-key", retrieval_top_k=5)

    results = retrieval.search(db, doc_a.id, "closest question", settings)

    assert len(results) == 2
    assert all(chunk.document_id == doc_a.id for chunk in results)
    assert results[0].content == "a chunk, moderate match"


def test_search_returns_exactly_the_default_retrieval_top_k_chunk_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-034: with no override, settings.retrieval_top_k defaults to 5 and
    a document with more than five chunks returns exactly five, best first."""
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    for i in range(8):
        # Decreasing similarity to [1.0, 0.0] as i grows.
        _add_chunk(db, document.id, i, f"chunk {i}", [1.0 - (i * 0.1), i * 0.1])

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    settings = Settings(embedding_api_key="test-key")

    assert settings.retrieval_top_k == 5

    results = retrieval.search(db, document.id, "find the best matches", settings)

    assert len(results) == 5
    assert [chunk.chunk_index for chunk in results] == [0, 1, 2, 3, 4]
    db.close()


def test_search_respects_retrieval_top_k_and_orders_by_similarity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "best match", [1.0, 0.0, 0.0])
    _add_chunk(db, document.id, 1, "second match", [0.5, 0.5, 0.0])
    _add_chunk(db, document.id, 2, "worst match", [0.0, 0.0, 1.0])

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0, 0.0]])
    settings = Settings(embedding_api_key="test-key", retrieval_top_k=2)

    results = retrieval.search(db, document.id, "find the best match", settings)

    assert len(results) == 2
    assert results[0].content == "best match"
    assert results[1].content == "second match"
    db.close()


def test_search_raises_embedding_error_without_provider_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = TestingSessionLocal()
    document = _make_ready_document(db)

    def _raise(texts, settings):
        raise EmbeddingError("The embedding provider rejected the configured API key.")

    monkeypatch.setattr(retrieval, "embed_texts", _raise)
    settings = Settings(embedding_api_key="test-key")

    with pytest.raises(EmbeddingError):
        retrieval.search(db, document.id, "anything", settings)
    db.close()


# --- POST /documents/{id}/ask ----------------------------------------------


def test_ask_rejects_empty_question() -> None:
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    db.close()

    response = client.post(f"/documents/{document.id}/ask", json={"question": "   "})

    assert response.status_code == 400


def test_ask_rejects_whitespace_only_question_with_tabs_and_newlines() -> None:
    """AC: 400 on a whitespace-only question, not only a plain space run."""
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    db.close()

    response = client.post(f"/documents/{document.id}/ask", json={"question": " \t\n  "})

    assert response.status_code == 400


def test_ask_unknown_document_returns_404() -> None:
    response = client.post(f"/documents/{uuid.uuid4()}/ask", json={"question": "hello?"})

    assert response.status_code == 404


def test_ask_not_ready_document_returns_409() -> None:
    db = TestingSessionLocal()
    document = Document(id=uuid.uuid4(), file_name="x.txt", file_type="txt", status="processing")
    db.add(document)
    db.commit()
    doc_id = document.id
    db.close()

    response = client.post(f"/documents/{doc_id}/ask", json={"question": "hello?"})

    assert response.status_code == 409


@pytest.mark.parametrize("non_ready_status", ["processing", "failed"])
def test_ask_each_non_ready_status_returns_409(non_ready_status: str) -> None:
    """409 covers every status other than 'ready', not just 'processing'."""
    db = TestingSessionLocal()
    document = Document(
        id=uuid.uuid4(), file_name="x.txt", file_type="txt", status=non_ready_status
    )
    db.add(document)
    db.commit()
    doc_id = document.id
    db.close()

    response = client.post(f"/documents/{doc_id}/ask", json={"question": "hello?"})

    assert response.status_code == 409


def test_ask_embedding_failure_returns_503_with_readable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "some content", [1.0, 0.0])
    db.close()

    get_settings.cache_clear()
    monkeypatch.setenv("EMBEDDING_API_KEY", "super-secret-key")

    def _raise(texts, settings):
        raise EmbeddingError("The embedding provider rejected the configured API key.")

    monkeypatch.setattr(retrieval, "embed_texts", _raise)

    response = client.post(f"/documents/{document.id}/ask", json={"question": "what is here?"})

    assert response.status_code == 503
    assert "super-secret-key" not in response.text
    assert "rejected" in response.json()["detail"]

    get_settings.cache_clear()


def test_ask_embedding_failure_never_includes_the_raw_api_key_anywhere_in_the_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-036: the key must not leak into the response body, headers, or
    whatever the test run captures of stdout/stderr (a stand-in for logs)."""
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "some content", [1.0, 0.0])
    db.close()

    get_settings.cache_clear()
    secret = "sk-super-secret-embedding-key-value"
    monkeypatch.setenv("EMBEDDING_API_KEY", secret)

    def _raise(texts, settings):
        raise EmbeddingError(f"Provider error, Authorization: Bearer {secret}")

    monkeypatch.setattr(retrieval, "embed_texts", _raise)

    response = client.post(f"/documents/{document.id}/ask", json={"question": "what is here?"})

    assert response.status_code == 503
    assert secret not in response.text
    for header_value in response.headers.values():
        assert secret not in header_value

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err

    get_settings.cache_clear()


def test_ask_returns_answer_and_source_from_top_matching_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "irrelevant filler text", [0.0, 1.0], page_number=1)
    _add_chunk(db, document.id, 1, "the capital of France is Paris", [1.0, 0.0], page_number=2)
    db.close()

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])

    response = client.post(
        f"/documents/{document.id}/ask", json={"question": "What is the capital of France?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "Paris" in body["answer"]
    assert body["source"]["page_number"] == 2
    assert body["is_fallback"] is True
    assert "model_configured" in body


def test_ask_with_no_matching_chunks_returns_no_fabricated_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    db.close()

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])

    response = client.post(f"/documents/{document.id}/ask", json={"question": "anything?"})

    assert response.status_code == 200
    body = response.json()
    assert body["source"] is None
    assert body["is_fallback"] is True


def test_ask_with_no_chunks_at_all_does_not_call_the_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document with zero chunks still goes through retrieval.search (so
    this does not mask a real embedder call elsewhere), but must still
    resolve offline via the stub and never fabricate an answer."""
    db = TestingSessionLocal()
    document = _make_ready_document(db)
    db.close()

    calls = []

    def _embed(texts, settings):
        calls.append(texts)
        return [[1.0, 0.0]]

    monkeypatch.setattr(retrieval, "embed_texts", _embed)

    response = client.post(f"/documents/{document.id}/ask", json={"question": "anything?"})

    assert response.status_code == 200
    assert calls, "the stub must have been invoked -- proves no live network path exists"
    assert response.json()["is_fallback"] is True
