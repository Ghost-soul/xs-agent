from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GenerationBatchRecord(Base):
    __tablename__ = "generation_batches"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    revision: Mapped[str] = mapped_column(String(80))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    preview_sha256: Mapped[str] = mapped_column(String(64))
    authorized: Mapped[bool] = mapped_column(default=False)
    pause_requested: Mapped[bool] = mapped_column(default=False)
    next_action: Mapped[str | None] = mapped_column(String(20), default="plan")
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GenerationArtifactRecord(Base):
    __tablename__ = "generation_artifacts"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("generation_batches.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GenerationCallRecord(Base):
    __tablename__ = "generation_calls"
    __table_args__ = (UniqueConstraint("batch_id", "slot"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("generation_batches.id", ondelete="CASCADE"))
    slot: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_sha256: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectRecord(Base):
    __tablename__ = "story_projects"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectDeletionRecord(Base):
    __tablename__ = "project_deletions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('cleanup_pending', 'completed')", name="ck_project_deletion_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    request_key_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="cleanup_pending")
    counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    cost_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    retired_commands: Mapped[list[str]] = mapped_column(JSONB, default=list)
    cleanup_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    cleanup_error: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectSetupDraftRecord(Base):
    __tablename__ = "project_setup_drafts"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200))
    schema_version: Mapped[str] = mapped_column(String(40), default="project-setup-draft-v1")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    finalized_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("story_projects.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReadingBookmarkRecord(Base):
    __tablename__ = "reading_bookmarks"
    __table_args__ = (UniqueConstraint("project_id", "chapter_id", "anchor_sha256"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(String(40), default="reading-bookmark-v1")
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("chapter_revisions.id", ondelete="CASCADE")
    )
    version_number: Mapped[int] = mapped_column(Integer)
    offset: Mapped[int] = mapped_column(Integer, default=0)
    anchor_sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LocalTaskRecord(Base):
    __tablename__ = "local_tasks"
    __table_args__ = (
        Index("ix_local_tasks_claim", "status", "lease_expires_at", "created_at"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_local_tasks_progress"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_local_tasks_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(String(40), default="local-task-v1")
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    input_sha256: Mapped[str] = mapped_column(String(64))
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SearchDocumentRecord(Base):
    """Rebuildable, provider-free search projection for one source document."""

    __tablename__ = "search_documents"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "document_key",
            name="uq_search_documents_project_key",
        ),
        Index(
            "ix_search_documents_project_current_kind",
            "project_id",
            "is_current",
            "source_kind",
            "id",
        ),
        Index(
            "ix_search_documents_text_trgm",
            "normalized_text",
            postgresql_using="gin",
            postgresql_ops={"normalized_text": "gin_trgm_ops"},
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_search_documents_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(String(40), default="search-document-v1")
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE")
    )
    document_key: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(200))
    version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("state_versions.id", ondelete="CASCADE"), nullable=True
    )
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chapter_revisions.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500))
    normalized_text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    locator_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
    )
    is_current: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImportPreviewRecord(Base):
    __tablename__ = "import_previews"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(String(40), default="import-preview-v1")
    title: Mapped[str] = mapped_column(String(200))
    original_filename: Mapped[str] = mapped_column(String(300), default="")
    byte_size: Mapped[int] = mapped_column(Integer)
    upload_sha256: Mapped[str] = mapped_column(String(64))
    selected_encoding: Mapped[str] = mapped_column(String(30))
    parser_version: Mapped[str] = mapped_column(String(40))
    chapter_manifest_sha256: Mapped[str] = mapped_column(String(64))
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="previewed")
    finalized_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("story_projects.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IdempotencyKeyRecord(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("command", "key"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    command: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(200))
    request_fingerprint_sha256: Mapped[str | None] = mapped_column(String(64))
    request_schema_version: Mapped[str | None] = mapped_column(String(40))
    resource_scope: Mapped[str | None] = mapped_column(String(240))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StateVersionRecord(Base):
    __tablename__ = "state_versions"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("state_versions.id"))
    parent_number: Mapped[int | None] = mapped_column(Integer)
    rollback_of_id: Mapped[UUID | None] = mapped_column(ForeignKey("state_versions.id"))
    rollback_of_number: Mapped[int | None] = mapped_column(Integer)
    restart_of_id: Mapped[UUID | None] = mapped_column(ForeignKey("state_versions.id"))
    restart_of_number: Mapped[int | None] = mapped_column(Integer)
    restart_base_number: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    chapter_revisions: Mapped[dict[str, str]] = mapped_column(JSONB)
    chapter_titles: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChapterRecord(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("project_id", "ordinal"),
        UniqueConstraint("project_id", "display_ordinal"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    display_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    current_revision_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class ChapterBriefRecord(Base):
    __tablename__ = "chapter_briefs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    supersedes_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapter_briefs.id"))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BriefPlanningSessionRecord(Base):
    __tablename__ = "brief_planning_sessions"
    __table_args__ = (
        Index("ix_brief_planning_sessions_chapter_created", "chapter_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    inspiration: Mapped[str] = mapped_column(Text)
    must_include: Mapped[list[str]] = mapped_column(JSONB)
    avoid: Mapped[list[str]] = mapped_column(JSONB)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(160))
    actual_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="exploring")
    finalized_brief_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapter_briefs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChapterRevisionRecord(Base):
    __tablename__ = "chapter_revisions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    brief_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapter_briefs.id"))
    supersedes_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapter_revisions.id"))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StateDeltaRecord(Base):
    __tablename__ = "state_deltas"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("chapter_revisions.id", ondelete="CASCADE"), unique=True
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_target_created", "target_type", "target_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    decision: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceSpanRecord(Base):
    __tablename__ = "evidence_spans"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    delta_id: Mapped[UUID] = mapped_column(ForeignKey("state_deltas.id", ondelete="CASCADE"))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)


class BaselineGenerationPlanRecord(Base):
    __tablename__ = "baseline_generation_plans"
    __table_args__ = (Index("ix_baseline_generation_plans_chapter_status", "chapter_id", "status"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    brief_id: Mapped[UUID] = mapped_column(ForeignKey("chapter_briefs.id"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30))
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    request_hash: Mapped[str] = mapped_column(String(64))
    estimated_input_tokens: Mapped[int] = mapped_column(Integer)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    estimated_cost_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    actual_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    revision_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapter_revisions.id"))
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    provider_output_sha256: Mapped[str | None] = mapped_column(String(64))
    validation_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRunRecord(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_brief_created", "brief_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    brief_id: Mapped[UUID] = mapped_column(ForeignKey("chapter_briefs.id"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    generation_plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_generation_plans.id"))
    status: Mapped[str] = mapped_column(String(30))
    max_rounds: Mapped[int] = mapped_column(Integer, default=2)
    revision_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapter_revisions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentArtifactRecord(Base):
    __tablename__ = "agent_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "role", "round_number", "kind"),
        Index("ix_agent_artifacts_run_round", "run_id", "round_number"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(30))
    round_number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentGenerationPlanRecord(Base):
    __tablename__ = "agent_generation_plans"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    brief_id: Mapped[UUID] = mapped_column(ForeignKey("chapter_briefs.id"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30))
    role_output_limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estimated_input_tokens: Mapped[int] = mapped_column(Integer)
    estimated_output_tokens: Mapped[int] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    estimated_cost_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentModelCallRecord(Base):
    __tablename__ = "agent_model_calls"
    __table_args__ = (UniqueConstraint("run_id", "role", "round_number"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(30))
    round_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    request_hash: Mapped[str] = mapped_column(String(64))
    provider_output_sha256: Mapped[str | None] = mapped_column(String(64))
    actual_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    actual_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WritingSessionRecord(Base):
    __tablename__ = "writing_sessions"
    __table_args__ = (Index("ix_writing_sessions_project_created", "project_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    direction: Mapped[str] = mapped_column(Text)
    author_guidance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    story_intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    must_include: Mapped[list[str]] = mapped_column(JSONB)
    avoid: Mapped[list[str]] = mapped_column(JSONB)
    target_min_chars: Mapped[int] = mapped_column(Integer)
    target_max_chars: Mapped[int] = mapped_column(Integer)
    scene_target_chars: Mapped[int] = mapped_column(Integer, default=4000)
    chapter_min_chars: Mapped[int] = mapped_column(Integer, default=4000)
    chapter_target_chars: Mapped[int] = mapped_column(Integer)
    chapter_max_chars: Mapped[int] = mapped_column(Integer, default=6000)
    status: Mapped[str] = mapped_column(String(30), default="writing")
    stage: Mapped[str] = mapped_column(String(30), default="writing")
    raw_body: Mapped[str | None] = mapped_column(Text)
    raw_sha256: Mapped[str | None] = mapped_column(String(64))
    review_package: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectStyleProfileRecord(Base):
    __tablename__ = "project_style_profiles"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"), primary_key=True
    )
    selection_mode: Mapped[str] = mapped_column(String(20), default="unselected")
    genre_id: Mapped[str] = mapped_column(String(40))
    secondary_genre_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    assets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectCreativePolicyRecord(Base):
    __tablename__ = "project_creative_policies"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"), primary_key=True
    )
    target_chapters: Mapped[int] = mapped_column(Integer)
    scene_target_chars: Mapped[int] = mapped_column(Integer)
    chapter_min_chars: Mapped[int] = mapped_column(Integer, default=4000)
    chapter_target_chars: Mapped[int] = mapped_column(Integer)
    chapter_max_chars: Mapped[int] = mapped_column(Integer, default=6000)
    target_min_chars: Mapped[int] = mapped_column(Integer)
    target_max_chars: Mapped[int] = mapped_column(Integer)
    minimum_follow_read_signal: Mapped[str] = mapped_column(String(20))
    stop_on_blocking_risk: Mapped[bool] = mapped_column(default=True)
    target_cost_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    soft_budget_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    hard_safety_ceiling_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectLocalModelProfileRecord(Base):
    __tablename__ = "project_local_model_profiles"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(default=False)
    base_url: Mapped[str] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(160))
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    preferred_tasks: Mapped[list[str]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class QualityLabRecord(Base):
    __tablename__ = "quality_lab_records"
    __table_args__ = (
        Index("ix_quality_lab_project_kind_created", "project_id", "kind", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RealReaderNoteRecord(Base):
    __tablename__ = "real_reader_notes"
    __table_args__ = (
        Index("ix_real_reader_notes_project_recorded", "project_id", "recorded_at"),
        Index("ix_real_reader_notes_project_chapter", "project_id", "chapter_id"),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_real_reader_notes_status",
        ),
        UniqueConstraint(
            "project_id",
            "note_fingerprint_sha256",
            name="uq_real_reader_notes_project_fingerprint",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(
        String(40), default="real-reader-note-v1", nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id"))
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("chapter_revisions.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    body_sha256: Mapped[str] = mapped_column(String(64))
    note_fingerprint_sha256: Mapped[str] = mapped_column(String(64))
    reader_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReaderCalibrationRuleRecord(Base):
    __tablename__ = "reader_calibration_rules"
    __table_args__ = (
        Index("ix_reader_calibration_rules_project_status", "project_id", "status"),
        UniqueConstraint(
            "project_id",
            "rule_version",
            name="uq_reader_calibration_rules_project_version",
        ),
        CheckConstraint(
            "status IN ('advisory', 'active', 'superseded', 'rolled_back')",
            name="ck_reader_calibration_rules_status",
        ),
        Index(
            "uq_reader_calibration_rules_one_active",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(
        String(40), default="reader-calibration-rule-v1", nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    rule_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="advisory", nullable=False)
    sample_sha256: Mapped[str] = mapped_column(String(64))
    report_sha256: Mapped[str] = mapped_column(String(64))
    sample_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rule_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rule_payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    based_on_rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reader_calibration_rules.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NovelAutomationRunRecord(Base):
    __tablename__ = "novel_automation_runs"
    __table_args__ = (
        Index("ix_novel_automation_runs_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    current_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("writing_sessions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="active")
    phase: Mapped[str] = mapped_column(String(40), default="plan_chapter")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RunAuthorizationRecord(Base):
    __tablename__ = "run_authorizations"
    __table_args__ = (
        UniqueConstraint("run_id", "envelope_sha256", name="uq_run_authorization_envelope"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("novel_automation_runs.id", ondelete="CASCADE")
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id"))
    base_version_id: Mapped[UUID] = mapped_column(ForeignKey("state_versions.id"))
    run_spec_sha256: Mapped[str] = mapped_column(String(64))
    provider_profile_revision: Mapped[str] = mapped_column(String(160))
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    envelope_sha256: Mapped[str] = mapped_column(String(64))
    total_cost_ceiling_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    stop_policy: Mapped[str] = mapped_column(String(80))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthorizedActionSlotRecord(Base):
    __tablename__ = "authorized_action_slots"
    __table_args__ = (
        UniqueConstraint(
            "authorization_id",
            "logical_action_id",
            name="uq_authorized_action_slot_logical_id",
        ),
        UniqueConstraint("reservation_id", name="uq_authorized_action_slot_reservation"),
        Index("ix_authorized_action_slots_authorization_status", "authorization_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    authorization_id: Mapped[UUID] = mapped_column(
        ForeignKey("run_authorizations.id", ondelete="CASCADE")
    )
    logical_action_id: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(20))
    action_kind: Mapped[str] = mapped_column(String(80))
    ordinal: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    prompt_version_id: Mapped[UUID] = mapped_column(ForeignKey("agent_prompt_versions.id"))
    max_input_tokens: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    visible_content_limit: Mapped[int] = mapped_column(Integer)
    reasoning_reserve: Mapped[int] = mapped_column(Integer)
    provider_output_limit: Mapped[int] = mapped_column(Integer)
    max_cost_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    data_scope_sha256: Mapped[str] = mapped_column(String(64))
    optional: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(30), default="authorized")
    reservation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunAutomationRecord(Base):
    __tablename__ = "run_automation_states"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_automation_state_run"),
        UniqueConstraint("idempotency_key_hash", name="uq_run_automation_idempotency"),
        Index("ix_run_automation_claim", "status", "lease_expires_at"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("novel_automation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    authorization_id: Mapped[UUID] = mapped_column(ForeignKey("run_authorizations.id"))
    mode: Mapped[str] = mapped_column(String(30), default="run_to_review")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    envelope_sha256: Mapped[str] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_decision_sha256: Mapped[str | None] = mapped_column(String(64))
    last_completed_action_id: Mapped[str | None] = mapped_column(String(160))
    pause_code: Mapped[str | None] = mapped_column(String(100))
    pause_details_sha256: Mapped[str | None] = mapped_column(String(64))
    safety_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReferenceCorpusManifestRecord(Base):
    __tablename__ = "reference_corpus_manifests"
    __table_args__ = (
        UniqueConstraint("project_id", "source_sha256", name="uq_reference_corpus_project_sha"),
        Index("ix_reference_corpus_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    source_path: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(240))
    source_sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    source_mtime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    encoding: Mapped[str] = mapped_column(String(30))
    parser_version: Mapped[str] = mapped_column(String(60))
    local_analysis_allowed: Mapped[bool] = mapped_column(default=False)
    provider_excerpt_allowed: Mapped[bool] = mapped_column(default=False)
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    parse_report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReferenceStyleSampleRecord(Base):
    __tablename__ = "reference_style_samples"
    __table_args__ = (
        Index("ix_reference_style_samples_manifest_category", "manifest_id", "category"),
        CheckConstraint(
            "status IN ('candidate', 'approved', 'excluded', 'false_positive')",
            name="ck_reference_style_samples_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    manifest_id: Mapped[UUID] = mapped_column(
        ForeignKey("reference_corpus_manifests.id", ondelete="CASCADE")
    )
    category: Mapped[str] = mapped_column(String(40))
    chapter_ordinal: Mapped[int] = mapped_column(Integer)
    chapter_title: Mapped[str] = mapped_column(String(200))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    cleaned_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    approved_dimensions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    prohibited_transfer: Mapped[list[str]] = mapped_column(JSONB, default=list)
    author_note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReferenceStyleProfileRecord(Base):
    __tablename__ = "reference_style_profiles"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_reference_style_profile_version"),
        Index("ix_reference_style_profile_project_status", "project_id", "status"),
        CheckConstraint(
            "status IN ('draft', 'active', 'superseded')",
            name="ck_reference_style_profiles_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    manifest_id: Mapped[UUID] = mapped_column(ForeignKey("reference_corpus_manifests.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    structural_ranges: Mapped[dict[str, Any]] = mapped_column(JSONB)
    positive_contract: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    negative_transfer_rules: Mapped[list[str]] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    blind_test: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GlobalReferenceStyleDefaultRecord(Base):
    __tablename__ = "global_reference_style_defaults"

    key: Mapped[str] = mapped_column(String(30), primary_key=True, default="writer")
    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("reference_style_profiles.id", ondelete="RESTRICT"), unique=True
    )
    author_reason: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectNaturalnessPolicyRecord(Base):
    __tablename__ = "project_naturalness_policies"
    __table_args__ = (
        CheckConstraint(
            "sensitivity IN ('conservative', 'balanced', 'sensitive')",
            name="ck_naturalness_policy_sensitivity",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("story_projects.id", ondelete="CASCADE"), primary_key=True
    )
    reference_style_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reference_style_profiles.id"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    sensitivity: Mapped[str] = mapped_column(String(20), default="balanced")
    diagnostic_only: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NaturalnessRegressionSampleRecord(Base):
    __tablename__ = "naturalness_regression_samples"
    __table_args__ = (
        Index(
            "ix_naturalness_samples_project_decision",
            "project_id",
            "decision",
            "created_at",
        ),
        UniqueConstraint(
            "project_id",
            "body_sha256",
            "start_offset",
            "end_offset",
            "issue_family",
            name="uq_naturalness_sample_evidence",
        ),
        CheckConstraint(
            "decision IN ('should-flag', 'should-not-flag', 'protection')",
            name="ck_naturalness_sample_decision",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chapter_revisions.id"), nullable=True
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("writing_sessions.id"), nullable=True
    )
    body_sha256: Mapped[str] = mapped_column(String(64))
    issue_family: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(30))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    suggested_action: Mapped[str] = mapped_column(String(40), default="manual-review")
    protected_content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    author_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NovelAutomationCheckpointRecord(Base):
    __tablename__ = "novel_automation_checkpoints"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_novel_run_checkpoint_sequence"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("novel_automation_runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WritingChunkRecord(Base):
    __tablename__ = "writing_chunks"
    __table_args__ = (UniqueConstraint("session_id", "ordinal"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("writing_sessions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WritingArtifactRecord(Base):
    __tablename__ = "writing_artifacts"
    __table_args__ = (UniqueConstraint("session_id", "stage", "kind"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("writing_sessions.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WritingCostPlanRecord(Base):
    __tablename__ = "writing_cost_plans"
    __table_args__ = (Index("ix_writing_cost_plans_session_created", "session_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("writing_sessions.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="pending_confirmation")
    role_limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    pricing: Mapped[dict[str, Any]] = mapped_column(JSONB)
    data_scope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estimated_input_tokens: Mapped[int] = mapped_column(Integer)
    estimated_output_tokens: Mapped[int] = mapped_column(Integer)
    estimated_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pricing_known: Mapped[bool] = mapped_column(default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentPromptVersionRecord(Base):
    __tablename__ = "agent_prompt_versions"
    __table_args__ = (
        UniqueConstraint("role", "version", name="uq_agent_prompt_role_version"),
        CheckConstraint(
            "role IN ('director', 'writer', 'memory', 'checker', 'editor', 'reader')",
            name="ck_agent_prompt_versions_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    role: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer)
    system_template: Mapped[str] = mapped_column(Text)
    template_sha256: Mapped[str] = mapped_column(String(64))
    input_contract: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output_contract: Mapped[dict[str, Any]] = mapped_column(JSONB)
    author_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentPromptActivationRecord(Base):
    __tablename__ = "agent_prompt_activations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('director', 'writer', 'memory', 'checker', 'editor', 'reader')",
            name="ck_agent_prompt_activations_role",
        ),
        Index("ix_agent_prompt_activation_role_created", "role", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    role: Mapped[str] = mapped_column(String(20))
    prompt_version_id: Mapped[UUID] = mapped_column(ForeignKey("agent_prompt_versions.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentCallRecord(Base):
    __tablename__ = "writing_chunk_calls"
    __table_args__ = (
        UniqueConstraint("session_id", "role", "call_key", name="uq_agent_calls_session_role_key"),
        CheckConstraint(
            "role IN ('director', 'writer', 'memory', 'checker', 'editor', 'reader')",
            name="ck_agent_calls_role",
        ),
        CheckConstraint(
            "role <> 'writer' OR ordinal IS NOT NULL",
            name="ck_agent_calls_writer_ordinal",
        ),
        Index("ix_agent_calls_session_role_started", "session_id", "role", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("writing_sessions.id", ondelete="CASCADE"))
    cost_plan_id: Mapped[UUID] = mapped_column(ForeignKey("writing_cost_plans.id"))
    role: Mapped[str] = mapped_column(String(20))
    call_key: Mapped[str] = mapped_column(String(160))
    ordinal: Mapped[int | None] = mapped_column(Integer)
    prompt_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_prompt_versions.id"))
    rendered_prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    authorization_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_authorizations.id"))
    logical_action_id: Mapped[str | None] = mapped_column(String(160))
    reservation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    request_sha256: Mapped[str] = mapped_column(String(64))
    context_sha256: Mapped[str] = mapped_column(String(64))
    max_input_tokens: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    max_content_tokens: Mapped[int | None] = mapped_column(Integer)
    max_cost_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    raw_response_sha256: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    raw_transport_sha256: Mapped[str | None] = mapped_column(String(64))
    raw_transport_format: Mapped[str | None] = mapped_column(String(80))
    raw_entity_body_sha256: Mapped[str | None] = mapped_column(String(64))
    response_headers_redacted: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    extracted_content_sha256: Mapped[str | None] = mapped_column(String(64))
    validation_report_sha256: Mapped[str | None] = mapped_column(String(64))
    actual_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChapterTitleBatchCallRecord(Base):
    """Auditable Provider operation for title batches; deliberately not an Agent role."""

    __tablename__ = "chapter_title_batch_calls"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "idempotency_key_hash",
            name="uq_title_batch_session_idempotency",
        ),
        UniqueConstraint(
            "session_id",
            "review_sha256",
            "attempt",
            name="uq_title_batch_session_review_attempt",
        ),
        CheckConstraint(
            "status IN ('executing', 'completed', 'completed_stale', "
            "'failed_before_dispatch', 'response_invalid', 'outcome_uncertain')",
            name="ck_title_batch_status",
        ),
        Index(
            "ix_title_batch_session_started",
            "session_id",
            "started_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("writing_sessions.id", ondelete="CASCADE"))
    cost_plan_id: Mapped[UUID] = mapped_column(ForeignKey("writing_cost_plans.id"))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    review_sha256: Mapped[str] = mapped_column(String(64))
    context_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    request_contexts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    max_input_tokens: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    reserved_cost_cny: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    raw_response_sha256: Mapped[str | None] = mapped_column(String(64))
    actual_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChapterSummaryRecord(Base):
    """S3: Structured summary for each formally merged chapter."""

    __tablename__ = "chapter_summaries"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "chapter_number",
            name="uq_chapter_summaries_project_chapter",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("story_projects.id", ondelete="CASCADE"))
    chapter_number: Mapped[int] = mapped_column(Integer)
    volume_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    key_events: Mapped[list[str]] = mapped_column(JSONB, default=list)
    characters_present: Mapped[list[str]] = mapped_column(JSONB, default=list)
    character_changes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    promises_touched: Mapped[list[str]] = mapped_column(JSONB, default=list)
    foreshadowings_touched: Mapped[list[str]] = mapped_column(JSONB, default=list)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    time_position: Mapped[str | None] = mapped_column(String(200), nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    narrative_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_handoff: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chapter_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContextCompactionRecord(Base):
    """S2: Persisted context compaction summaries for progressive compression."""

    __tablename__ = "context_compactions"
    __table_args__ = (
        Index("ix_context_compactions_session_type", "session_id", "source_type"),
        UniqueConstraint(
            "session_id",
            "source_type",
            "source_chapter_range",
            name="uq_context_compactions_call_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("writing_sessions.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(50))
    source_chapter_range: Mapped[str | None] = mapped_column(String(20), nullable=True)
    original_tokens: Mapped[int] = mapped_column(Integer)
    summary_tokens: Mapped[int] = mapped_column(Integer)
    summary_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Compatibility import for code and audit tooling written before the six-role ledger.
WritingChunkCallRecord = AgentCallRecord
