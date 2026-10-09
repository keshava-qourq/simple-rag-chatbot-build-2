"""Engine, session factory and the request-scoped session dependency.

SQLite by default so a fresh clone runs with nothing but `pip install -r
requirements.txt` -- the approved architecture may name Postgres or MySQL, but
naming one is not the same as having one, and a scaffold that cannot start
without a database server is a scaffold nobody runs. Point `DATABASE_URL` at
the real thing when it exists; nothing else has to change.

The default database file lives at a fixed, documented path on local disk
(`backend/data/app.db`) -- resolved from this module's own location, not the
process's current working directory, so it is the same path whether the app
is started from `backend/` or from the repo root, and across a restart
(AC-009-1 / AC-027). It is never inside a temp directory, so it survives a
process stop and restart. The directory is created here on first run, and
`app.main` only ever calls `Base.metadata.create_all`, which creates missing
tables and leaves existing ones -- and their rows -- untouched; the schema is
never dropped or recreated on start.
"""

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# backend/app/database.py -> backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _BACKEND_DIR / "data" / "app.db"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}")


def _ensure_sqlite_directory_exists(database_url: str) -> None:
    """Create the parent directory of a `sqlite:///...` file, if missing.

    A no-op for an in-memory database or any non-SQLite `DATABASE_URL` --
    e.g. a real Postgres/MySQL server, which owns its own storage.
    """
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw_path = database_url[len(prefix) :]
    if raw_path in ("", ":memory:"):
        return
    Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_directory_exists(DATABASE_URL)

# SQLite rejects a connection made on one thread and used on another, which is
# exactly what happens when FastAPI runs a sync dependency in its threadpool.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base that every generated model inherits."""


def get_db() -> Iterator[Session]:
    """One session per request, closed even when the handler raises."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
