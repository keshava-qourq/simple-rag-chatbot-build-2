"""Engine, session factory and the request-scoped session dependency.

SQLite by default so a fresh clone runs with nothing but `pip install -r
requirements.txt` -- the approved architecture may name Postgres or MySQL, but
naming one is not the same as having one, and a scaffold that cannot start
without a database server is a scaffold nobody runs. Point `DATABASE_URL` at
the real thing when it exists; nothing else has to change.
"""

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

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
