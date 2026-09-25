"""Derived index tables. All rows have explicit novel ownership."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from novel_writer.db.models import Base


class Vector(UserDefinedType[Any]):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector(512)"


class KnowledgeIndexRecord(Base):
    __tablename__ = "knowledge_indexes"

    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("state_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    model_key: Mapped[str | None] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeChunkRecord(Base):
    __tablename__ = "knowledge_chunks"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("state_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text_sha256: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class KnowledgeVectorRecord(Base):
    __tablename__ = "knowledge_vectors"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    model_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    embedding: Mapped[Any] = mapped_column(Vector())
