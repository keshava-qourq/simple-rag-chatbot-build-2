"""Restart-persistence tests exercised through the real app module and
session factory, not a bare SQLAlchemy engine (US-009-2).

`test_persistence.py` already proves AC-027/AC-028 against raw
`create_engine`/`sessionmaker` calls. This file proves the same guarantees
one layer up: a document uploaded, embedded and asked through one FastAPI
`app` instance is still present, `status='ready'` and answerable through a
*second*, freshly rebuilt `app` instance -- `app.database`, `app.models` and
every module that depends on them reloaded in dependency order, the nearest
a single test process gets to "the app module and session factory are
re-initialised against the same on-disk database" without literally
exec-ing a new process.

AC-027: a document and its chunks survive that reinitialisation, are still
`status='ready'`, carry the same `chunk_count` as before, and the document
is still questionable (`POST /documents/{id}/ask` returns 200) afterwards,
with no re-processing or re-embedding of the stored chunks.

AC-028: no endpoint on the rebuilt app stores or returns chat messages.

The embedding provider is always stubbed via `monkeypatch.setattr(...,
"embed_texts", ...)` on the reloaded `doc_processor`/`retrieval` modules --
this suite never reaches a network.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


def _reload_app_stack():
    """Reload, in dependency order, every module between `app.database`
    and `app.main` so a fresh engine, session factory, ORM mapper registry
    and FastAPI `app` are rebuilt from the *current* environment -- the
    same sequence a real process runs at start-up, run again inside this
    one test process instead of a new one."""
    import app.config as config
    import app.database as database
    import app.models as models
    import app.routers.config as config_router
    import app.routers.documents as documents_router
    import app.services.doc_processor as doc_processor
    import app.services.embedder as embedder
    import app.services.retrieval as retrieval
    import app.services.storage as storage

    config.get_settings.cache_clear()
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(storage)
    importlib.reload(embedder)
    importlib.reload(doc_processor)
    importlib.reload(retrieval)
    importlib.reload(documents_router)
    importlib.reload(config_router)

    import app.main as main

    importlib.reload(main)

    return main, documents_router, doc_processor, retrieval


@pytest.fixture
def restart_env(tmp_path, monkeypatch):
    """Points `DATABASE_URL`/`DOCUMENT_STORAGE_DIR` at a tmp on-disk
    location (never the developer's real `backend/data/app.db`) and
    restores every reloaded module back to the real defaults once the test
    is done, so no other test in the suite is left pointed at a deleted tmp
    database."""
    db_path = tmp_path / "restart.db"
    storage_dir = tmp_path / "documents"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("DOCUMENT_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    yield db_path
    # Restore the real environment *before* reloading, so the final reload
    # rebuilds every module against the real on-disk defaults again.
    monkeypatch.undo()
    _reload_app_stack()


def _stub_embeddings(doc_processor_module, retrieval_module) -> None:
    def fake_embed_many(texts, _settings):
        return [[float(i), 1.0] for i, _ in enumerate(texts)]

    def fake_embed_one(texts, _settings):
        return [[1.0, 1.0]]

    doc_processor_module.embed_texts = fake_embed_many
    retrieval_module.embed_texts = fake_embed_one


def test_document_and_chunks_survive_reinitialisation_and_stay_questionable(
    restart_env,
) -> None:
    """AC-027: written through one app/session lifecycle, still present,
    `status='ready'`, with the same `chunk_count`, and answerable after the
    app module and session factory are rebuilt against the same on-disk
    database file."""
    main_a, _documents_router_a, doc_processor_a, retrieval_a = _reload_app_stack()
    _stub_embeddings(doc_processor_a, retrieval_a)
    client_a = TestClient(main_a.app)

    upload = client_a.post(
        "/documents",
        files={"file": ("notes.txt", b"hello world, durable content", "text/plain")},
    )
    assert upload.status_code == 202
    body = upload.json()
    assert body["status"] == "ready"
    document_id = body["id"]

    before_status = client_a.get(f"/documents/{document_id}").json()
    assert before_status["chunk_count"] > 0

    ask_before = client_a.post(f"/documents/{document_id}/ask", json={"question": "hello?"})
    assert ask_before.status_code == 200

    # --- "restart": rebuild the engine, session factory, ORM registry and
    # FastAPI app from scratch, against the same on-disk database file ---
    main_b, _documents_router_b, doc_processor_b, retrieval_b = _reload_app_stack()
    _stub_embeddings(doc_processor_b, retrieval_b)
    client_b = TestClient(main_b.app)

    listing = client_b.get("/documents")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    status_response = client_b.get(f"/documents/{document_id}")
    assert status_response.status_code == 200
    restarted = status_response.json()
    assert restarted["status"] == "ready"
    assert restarted["chunk_count"] == before_status["chunk_count"]

    ask_after = client_b.post(f"/documents/{document_id}/ask", json={"question": "hello again?"})
    assert ask_after.status_code == 200
    assert ask_after.json()["answer"]


def test_reinitialisation_neither_drops_nor_recreates_existing_rows(restart_env) -> None:
    """Start-up against an existing database file must not wipe or
    re-process what is already there -- two documents before, the exact
    same two (by id and chunk_count) after."""
    main_a, _, doc_processor_a, retrieval_a = _reload_app_stack()
    _stub_embeddings(doc_processor_a, retrieval_a)
    client_a = TestClient(main_a.app)

    client_a.post(
        "/documents",
        files={"file": ("one.txt", b"first document content here", "text/plain")},
    )
    client_a.post(
        "/documents",
        files={"file": ("two.txt", b"second document content here", "text/plain")},
    )
    before = client_a.get("/documents").json()
    before_ids = {doc["id"] for doc in before}
    before_counts = {doc["id"]: doc["chunk_count"] for doc in before}
    assert len(before_ids) == 2

    main_b, _, doc_processor_b, retrieval_b = _reload_app_stack()
    _stub_embeddings(doc_processor_b, retrieval_b)
    client_b = TestClient(main_b.app)

    after = client_b.get("/documents").json()
    after_ids = {doc["id"] for doc in after}
    after_counts = {doc["id"]: doc["chunk_count"] for doc in after}

    assert after_ids == before_ids
    assert after_counts == before_counts


def test_deleted_document_does_not_reappear_after_reinitialisation(restart_env) -> None:
    main_a, _, doc_processor_a, retrieval_a = _reload_app_stack()
    _stub_embeddings(doc_processor_a, retrieval_a)
    client_a = TestClient(main_a.app)

    created = client_a.post(
        "/documents",
        files={"file": ("gone.txt", b"this will be deleted", "text/plain")},
    ).json()
    document_id = created["id"]

    delete_response = client_a.delete(f"/documents/{document_id}")
    assert delete_response.status_code == 204

    main_b, _, doc_processor_b, retrieval_b = _reload_app_stack()
    _stub_embeddings(doc_processor_b, retrieval_b)
    client_b = TestClient(main_b.app)

    assert client_b.get(f"/documents/{document_id}").status_code == 404
    assert client_b.get("/documents").json() == []


def test_no_endpoint_stores_or_returns_chat_messages(restart_env) -> None:
    """AC-028: the rebuilt app exposes no endpoint that stores or returns
    chat messages -- checked against the app actually reconstructed through
    this same reinitialisation path, not only the module-import-time one."""
    main_b, _, _, _ = _reload_app_stack()
    client_b = TestClient(main_b.app)

    openapi = client_b.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]

    assert not any("message" in path.lower() for path in paths)
    assert not any("thread" in path.lower() for path in paths)
    assert not any("chat" in path.lower() for path in paths)

    routes = {route.path for route in main_b.app.routes}
    assert not any("message" in path.lower() for path in routes)
