"""ORM model for bounded optimization workflow runs (`evaluation_round` 1..3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    column,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtColumn, UTCDateTime, UuidPrimaryKey
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT, WorkflowRunStatus

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.evaluation import Evaluation
    from app.models.prompt_request import PromptRequest
    from app.models.prompt_version import PromptVersion


class WorkflowRun(Base):
    """Tracks a bounded prompt optimization run with at most three evaluation rounds."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_prompt_request_id", "prompt_request_id"),
        Index("ix_workflow_runs_status", "status"),
        Index(
            "uq_workflow_runs_active_prompt_request",
            "prompt_request_id",
            unique=True,
            sqlite_where=column("status").in_(("PENDING", "GENERATING", "EVALUATING", "REVISING")),
            postgresql_where=column("status").in_(
                ("PENDING", "GENERATING", "EVALUATING", "REVISING")
            ),
        ),
        UniqueConstraint(
            "prompt_request_id",
            "client_idempotency_key_digest",
            name="uq_workflow_runs_request_idempotency",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'GENERATING', 'EVALUATING', 'REVISING', "
            "'COMPLETED', 'FAILED', 'CLARIFICATION_REQUIRED')",
            name="status_valid",
        ),
        CheckConstraint("state_revision >= 0", name="state_revision_non_negative"),
        CheckConstraint(
            "request_content_digest IS NULL OR length(request_content_digest) = 64",
            name="request_digest_length",
        ),
        CheckConstraint(
            "client_idempotency_key_digest IS NULL OR length(client_idempotency_key_digest) = 64",
            name="idempotency_digest_length",
        ),
        CheckConstraint(
            "(execution_token IS NULL AND lease_expires_at IS NULL) OR "
            "(execution_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="claim_lease_pair",
        ),
        CheckConstraint(
            f"max_evaluation_rounds BETWEEN 1 AND {MAX_EVALUATION_ROUNDS_HARD_LIMIT}",
            name="max_evaluation_rounds_range",
        ),
        CheckConstraint(
            "current_round >= 0 AND current_round <= max_evaluation_rounds",
            name="current_round_range",
        ),
        CheckConstraint(
            "completed_rounds >= 0 "
            "AND completed_rounds <= max_evaluation_rounds "
            "AND completed_rounds <= current_round",
            name="completed_rounds_range",
        ),
        CheckConstraint(
            "final_score IS NULL OR (final_score >= 0 AND final_score <= 100)",
            name="final_score_range",
        ),
    )

    id: Mapped[UuidPrimaryKey]
    prompt_request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("prompt_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=WorkflowRunStatus.PENDING.value,
    )
    max_evaluation_rounds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=MAX_EVALUATION_ROUNDS_HARD_LIMIT,
    )
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    request_content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    execution_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    client_idempotency_key_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminal_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    final_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "prompt_versions.id",
            name="fk_workflow_runs_final_version_id_prompt_versions",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    qualified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAtColumn]
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    prompt_request: Mapped[PromptRequest] = relationship(
        "PromptRequest",
        back_populates="workflow_runs",
        foreign_keys=[prompt_request_id],
    )
    prompt_versions: Mapped[list[PromptVersion]] = relationship(
        "PromptVersion",
        back_populates="workflow_run",
        foreign_keys="PromptVersion.workflow_run_id",
        order_by="PromptVersion.version_number",
        passive_deletes=True,
    )
    final_version: Mapped[PromptVersion | None] = relationship(
        "PromptVersion",
        foreign_keys=[final_version_id],
        post_update=True,
    )
    agent_runs: Mapped[list[AgentRun]] = relationship(
        "AgentRun",
        back_populates="workflow_run",
        foreign_keys="AgentRun.workflow_run_id",
        order_by="AgentRun.started_at",
        passive_deletes=True,
    )
    evaluations: Mapped[list[Evaluation]] = relationship(
        "Evaluation",
        back_populates="workflow_run",
        foreign_keys="Evaluation.workflow_run_id",
        order_by="Evaluation.evaluation_round",
        passive_deletes=True,
    )
