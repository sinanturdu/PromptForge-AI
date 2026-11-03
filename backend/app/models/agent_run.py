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
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, UuidPrimaryKey, utc_now
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT, AgentRunStatus

if TYPE_CHECKING:
    from app.models.prompt_version import PromptVersion
    from app.models.workflow_run import WorkflowRun


class AgentRun(Base):
    """Records execution metadata, provider parameters, and token usage for an agent call."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_workflow_run_id", "workflow_run_id"),
        Index("ix_agent_runs_agent_name", "agent_name"),
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
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AgentRunStatus.RUNNING.value,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    repair_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
