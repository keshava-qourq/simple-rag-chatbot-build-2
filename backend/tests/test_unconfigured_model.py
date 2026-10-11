"""Tests for US-017-2: the unconfigured-model path and the setup-artifact
guarantees that make it trustworthy.

Covers AC-049 (with `LLM_API_KEY` and `EMBEDDING_API_KEY` both unset, the app
still imports, starts, and serves `POST /documents`, `GET /documents` and
`GET /documents/{id}`), AC-050 (with no LLM configured,
`POST /documents/{id}/ask` returns the exact no-model contract -- never a
fabricated answer or leaked chunk text), AC-051 (with `LLM_API_KEY` set and
the provider client patched, a fresh `Settings`/app produces a real
generated answer with no notice text) and AC-061 (`requirements.txt` and
`.env.example` exist, every `Settings` field is covered by `.env.example`,
and neither file contains a real-secret-shaped value).

The embedding provider and the LLM provider client are always stubbed via
monkeypatch -- this suite never reaches the network -- and
`get_settings.cache_clear()` runs on both sides of every test that touches
the environment, so no env state leaks across tests.
"""

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models import Chunk, Document
from app.services import answer_gen, retrieval, storage
from app.services.answer_gen import NO_MODEL_MESSAGE

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = BACKEND_ROOT / "requirements.txt"
ENV_EXAMPLE_PATH = BACKEND_ROOT / ".env.example"

TEST_DB_URL = "sqlite:///./test_unconfigured_model.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{15,}"),
]


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
    get_settings.cache_clear()
    yield
    if previous_db_override is not None:
        app.dependency_overrides[get_db] = previous_db_override
    else:
        app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)
    Base.metadata.drop_all(bind=engine)
    get_settings.cache_clear()


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


# --- AC-049: the app imports and starts, and the CRUD surface still works --


def test_app_imports_and_crud_endpoints_succeed_with_no_keys_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.llm_configured is False
    assert settings.embedding_configured is False

    upload = client.post(
        "/documents",
        files={"file": ("notes.txt", b"hello world, no provider configured", "text/plain")},
    )
    assert upload.status_code == 202
    document_id = upload.json()["id"]

    listing = client.get("/documents")
    assert listing.status_code == 200
    assert any(doc["id"] == document_id for doc in listing.json())

    single = client.get(f"/documents/{document_id}")
    assert single.status_code == 200
    assert single.json()["id"] == document_id


# --- AC-050: no LLM configured -> the exact no-model contract -------------


def test_ask_with_no_llm_configured_returns_the_exact_no_model_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embed-key")
    get_settings.cache_clear()

    db = TestingSessionLocal()
    document = _make_ready_document(db)
    chunk_text = "the secret retrieved chunk text that must never leak into the answer"
    _add_chunk(db, document.id, 0, chunk_text, [1.0, 0.0])
    document_id = document.id
    db.close()

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("the provider client must never be called with no LLM configured")

    monkeypatch.setattr(answer_gen, "_call_llm", _fail_if_called)

    response = client.post(f"/documents/{document_id}/ask", json={"question": "what does it say?"})

    assert response.status_code == 200
    body = response.json()
    assert body["model_configured"] is False
    assert body["is_fallback"] is True
    assert body["source"] is None
    assert body["answer"] == NO_MODEL_MESSAGE
    assert chunk_text not in body["answer"]
    assert chunk_text not in response.text


def test_ask_with_no_llm_configured_never_calls_the_provider_client_even_with_strong_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strongly matching chunk still must not reach the LLM client when no
    model is configured -- the unconfigured check wins before generation."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embed-key")
    get_settings.cache_clear()

    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "a perfectly matching chunk of content", [1.0, 0.0])
    document_id = document.id
    db.close()

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    calls = []
    monkeypatch.setattr(
        answer_gen, "_call_llm", lambda q, chunks, settings: calls.append(1) or "unused"
    )

    response = client.post(f"/documents/{document_id}/ask", json={"question": "what is it?"})

    assert response.status_code == 200
    assert not calls
    assert response.json()["answer"] == NO_MODEL_MESSAGE


# --- AC-051: LLM_API_KEY set, provider client patched -> a real answer ----


def test_generate_answer_with_fresh_settings_and_patched_client_produces_generated_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embed-key")
    get_settings.cache_clear()
    fresh_settings = get_settings()
    assert fresh_settings.llm_configured is True

    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "the warranty lasts two years", [1.0, 0.0])

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    monkeypatch.setattr(answer_gen, "_call_llm", lambda q, chunks, settings: "Two years.")

    result = answer_gen.generate_answer(db, document, "How long is the warranty?", fresh_settings)
    db.close()

    assert result.answer == "Two years."
    assert result.is_fallback is False
    assert NO_MODEL_MESSAGE not in result.answer
    assert "not configured" not in result.answer.lower()


def test_ask_endpoint_with_fresh_app_and_patched_client_reports_model_configured_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embed-key")
    get_settings.cache_clear()

    db = TestingSessionLocal()
    document = _make_ready_document(db)
    _add_chunk(db, document.id, 0, "the warranty lasts two years", [1.0, 0.0])
    document_id = document.id
    db.close()

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts, settings: [[1.0, 0.0]])
    monkeypatch.setattr(answer_gen, "_call_llm", lambda q, chunks, settings: "Two years.")

    response = client.post(
        f"/documents/{document_id}/ask", json={"question": "How long is the warranty?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model_configured"] is True
    assert body["is_fallback"] is False
    assert body["answer"] == "Two years."
    assert NO_MODEL_MESSAGE not in body["answer"]


# --- AC-061: setup artifacts exist, are complete, and carry no real secret -


def test_requirements_and_env_example_files_exist() -> None:
    assert REQUIREMENTS_PATH.exists(), "backend/requirements.txt must exist"
    assert ENV_EXAMPLE_PATH.exists(), "backend/.env.example must exist"


def test_env_example_covers_every_settings_field() -> None:
    """Every field `Settings` reads from the environment must have a
    corresponding entry in `.env.example`, so a deployer configuring from
    that file alone can set anything the app actually reads."""
    env_example_text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    declared_vars = set()
    for line in env_example_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        declared_vars.add(stripped.split("=", 1)[0].strip())

    missing = []
    for field_name in Settings.model_fields:
        env_var_name = field_name.upper()
        if env_var_name not in declared_vars:
            missing.append(env_var_name)

    assert not missing, f".env.example is missing coverage for: {missing}"


def test_requirements_and_env_example_contain_no_real_secret_shaped_value() -> None:
    for path in (REQUIREMENTS_PATH, ENV_EXAMPLE_PATH):
        text = path.read_text(encoding="utf-8")
        offenders = [p.pattern for p in SECRET_PATTERNS if p.search(text)]
        assert not offenders, f"{path.name} contains secret-shaped value(s): {offenders}"
