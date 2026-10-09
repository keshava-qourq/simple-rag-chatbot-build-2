"""SQLAlchemy models.

Two tables, matching the approved data model exactly: the document library
(`documents`) and each document's embedded chunks (`chunks`). `Base` is
already bound to the engine in `app.database`; `main.py` imports this module
before it creates the schema.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector as PGVector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, TypeDecorator, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

__all__ = ["Base", "Document", "Chunk", "VectorType"]


class VectorType(TypeDecorator):
    """An embedding column, with the storage format chosen at the engine
    level -- not by a try/except at call time.

    On Postgres this delegates to `pgvector`'s native `VECTOR` column, so a
    real deployment gets pgvector's own storage and indexing. Everywhere
    else (SQLite, the default `DATABASE_URL`, has no vector column type at
    all) it falls back to a JSON-encoded float list in a plain `TEXT`
    column. `app.services.retrieval` computes cosine similarity in Python
    either way, so this choice only ever affects how a vector is stored on
    disk, never how a search is carried out.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGVector())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.loads(value)


class Document(Base):
    """One uploaded file in the library (data_model: `documents`)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # "processing" | "ready" | "failed" -- the development sprint owns the
    # transitions between them; this column only has to be wide enough to
    # hold whichever value `doc_processor` writes.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Chunk(Base):
    """One embedded chunk of a document (data_model: `chunks`).

    `ondelete="CASCADE"` on the foreign key, plus the ORM's own
    delete-orphan cascade above, are what "removing a document removes its
    chunks and vectors in one operation" (datastore) comes down to at the
    schema level; the delete handler itself is the development sprint's work.
    """

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Dimension intentionally left unspecified: the embedding model is an
    # environment choice (`Settings.embedding_model`), not a schema
    # constant. See `VectorType` for the Postgres/SQLite storage split.
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")
