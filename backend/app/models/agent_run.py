"""ORM model for individual AI agent execution telemetry and audit records."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, UuidPrimaryKey, utc_now
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT, AgentRunStatus

if TYPE_CHECKING:
    from app.models.evaluation import Evaluation
    from app.models.prompt_version import PromptVersion
    from app.models.workflow_run import WorkflowRun


class AgentRun(Base):
    """Records execution metadata, provider parameters, and token usage for an agent call."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_workflow_run_id", "workflow_run_id"),
        Index("ix_agent_runs_agent_name", "agent_name"),
        UniqueConstraint("id", "workflow_run_id", name="uq_agent_runs_id_workflow"),
        UniqueConstraint(
            "workflow_run_id", "operation_key", name="uq_agent_runs_workflow_operation"
        ),
        UniqueConstraint(
            "workflow_run_id",
            "revision_attempt_key",
            name="uq_agent_runs_workflow_revision_attempt",
        ),
        CheckConstraint(
            "operation_key IS NULL OR (length(operation_key) > 0 AND length(operation_key) <= 64)",
            name="operation_key_length",
        ),
        CheckConstraint(
            "revision_attempt_key IS NULL OR length(revision_attempt_key) = 64",
            name="revision_key_length",
        ),
        CheckConstraint(
            "feedback_projection_digest IS NULL OR length(feedback_projection_digest) = 64",
            name="feedback_digest_length",
        ),
        CheckConstraint(
            "result_kind IS NULL OR result_kind IN "
            "('candidate', 'unchanged', 'clarification', 'evaluation', 'failed')",
            name="result_kind_valid",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="status_valid",
        ),
        CheckConstraint(
            "evaluation_round IS NULL OR "
            f"(evaluation_round BETWEEN 1 AND {MAX_EVALUATION_ROUNDS_HARD_LIMIT})",
            name="evaluation_round_range",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="input_tokens_non_negative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="output_tokens_non_negative",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="latency_ms_non_negative",
        ),
    )

    id: Mapped[UuidPrimaryKey]
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    instruction_version: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AgentRunStatus.RUNNING.value,
    )
    attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repair_attempted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision_attempt_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_evaluation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "evaluations.id",
            use_alter=True,
            name="fk_agent_runs_baseline_evaluation_id_evaluations",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    result_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    binding_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    result_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    feedback_projection_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    workflow_run: Mapped[WorkflowRun] = relationship(
        "WorkflowRun",
        back_populates="agent_runs",
        foreign_keys=[workflow_run_id],
    )
    prompt_version: Mapped[PromptVersion | None] = relationship(
        "PromptVersion",
        back_populates="agent_run",
        foreign_keys="PromptVersion.agent_run_id",
        uselist=False,
    )
    evaluation: Mapped[Evaluation | None] = relationship(
        "Evaluation",
        back_populates="critic_agent_run",
        foreign_keys="Evaluation.critic_agent_run_id",
        uselist=False,
    )
    baseline_evaluation: Mapped[Evaluation | None] = relationship(
        "Evaluation",
        foreign_keys=[baseline_evaluation_id],
    )
