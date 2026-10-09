"""Tests for on-disk persistence across a process restart (US-009-1).

Covers AC-027 (a processed document's status and chunk_count survive a
restart with no re-processing or re-embedding), the requirement that the
default database and storage paths are stable, documented and outside any
temp directory, AC-028 (no chat history is persisted anywhere), and "a
deleted document stays deleted across restarts".
"""

import importlib
import tempfile
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base
from app.models import Chunk, Document


def test_default_database_path_is_stable_and_outside_tempdir(monkeypatch):
    """The default DATABASE_URL must point at a fixed location on local
    disk, not a temp directory, so restarting the process finds it again."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app.database as database

    importlib.reload(database)
    try:
        url = str(database.engine.url)
        temp_dir = tempfile.gettempdir()
        assert temp_dir not in url
        assert "app.db" in url
        assert "data" in url
    finally:
        importlib.reload(database)


def test_default_storage_dir_is_stable_and_outside_tempdir(monkeypatch):
    """The default document storage directory must be a fixed location on
    local disk, not a temp directory."""
    monkeypatch.delenv("DOCUMENT_STORAGE_DIR", raising=False)
    import app.services.storage as storage

    importlib.reload(storage)
    try:
        path = str(storage.STORAGE_DIR)
        temp_dir = tempfile.gettempdir()
        assert temp_dir not in path
        assert "documents" in path
    finally:
        importlib.reload(storage)


def test_restart_preserves_ready_document_status_and_chunk_count(tmp_path):
    """AC-027: a document at status='ready' before a restart is still
    'ready', with the same chunk_count, after the process (and its
    engine/session) restarts -- with no re-processing or re-embedding."""
    db_path = tmp_path / "restart.db"
    db_url = f"sqlite:///{db_path}"

    # --- "before restart": a document finishes processing and is committed ---
    engine_before = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine_before)
    SessionBefore = sessionmaker(bind=engine_before, autoflush=False, expire_on_commit=False)
    session = SessionBefore()

    document_id = uuid.uuid4()
    session.add(Document(id=document_id, file_name="notes.txt", file_type="txt", status="ready"))
    session.add(
        Chunk(
            id=uuid.uuid4(),
            document_id=document_id,
            chunk_index=0,
            content="hello",
            embedding=[0.1, 0.2],
        )
    )
    session.add(
        Chunk(
            id=uuid.uuid4(),
            document_id=document_id,
            chunk_index=1,
            content="world",
            embedding=[0.3, 0.4],
        )
    )
    session.commit()
    session.close()
    engine_before.dispose()

    # --- "after restart": a fresh engine/session against the same file,
    # running the same create-if-missing schema call `app.main` runs on
    # every start -- it must create nothing new here and drop nothing ---
    engine_after = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine_after)
    SessionAfter = sessionmaker(bind=engine_after, autoflush=False, expire_on_commit=False)
    session_after = SessionAfter()

    restarted_document = session_after.get(Document, document_id)
    assert restarted_document is not None
    assert restarted_document.status == "ready"
    chunk_count = session_after.query(Chunk).filter(Chunk.document_id == document_id).count()
    assert chunk_count == 2

    session_after.close()
    engine_after.dispose()


def test_deleted_document_stays_deleted_across_restart(tmp_path):
    """A document deleted before a restart must not reappear after it."""
    db_path = tmp_path / "restart_delete.db"
    db_url = f"sqlite:///{db_path}"

    engine_before = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine_before)
    SessionBefore = sessionmaker(bind=engine_before, autoflush=False, expire_on_commit=False)
    session = SessionBefore()

    document_id = uuid.uuid4()
    session.add(Document(id=document_id, file_name="gone.txt", file_type="txt", status="ready"))
    session.commit()
    session.delete(session.get(Document, document_id))
    session.commit()
    session.close()
    engine_before.dispose()

    engine_after = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine_after)
    SessionAfter = sessionmaker(bind=engine_after, autoflush=False, expire_on_commit=False)
    session_after = SessionAfter()

    assert session_after.get(Document, document_id) is None

    session_after.close()
    engine_after.dispose()


def test_no_message_table_exists_in_the_schema():
    """AC-028: no chat history is persisted anywhere -- there must be no
    message table in the schema at all, only the two documented ones."""
    table_names = set(Base.metadata.tables.keys())
    assert table_names == {"documents", "chunks"}
    for name in table_names:
        assert "message" not in name.lower()


def test_no_message_endpoints_are_registered():
    """AC-028: no message/thread/chat endpoints exist on the server -- a
    restart leaves the chat area empty by design because nothing was ever
    stored server-side to repopulate it from."""
    from app.main import app

    paths = {route.path for route in app.routes}
    assert not any("message" in path.lower() for path in paths)
    assert not any("thread" in path.lower() for path in paths)
    assert not any("chat" in path.lower() for path in paths)
