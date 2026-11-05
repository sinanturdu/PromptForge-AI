"""One completed independent evaluation; JSON content is validated by domain contracts."""

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
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtColumn, UTCDateTime, UuidPrimaryKey

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.prompt_version import PromptVersion
    from app.models.workflow_run import WorkflowRun


class Evaluation(Base):
    """Structural SQL checks do not replace binding, evidence or scoring validation."""

    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "evaluation_round", name="uq_evaluations_workflow_round"
        ),
        UniqueConstraint(
            "workflow_run_id", "prompt_version_id", name="uq_evaluations_workflow_version"
        ),
        UniqueConstraint(
            "workflow_run_id", "input_fingerprint", name="uq_evaluations_workflow_fingerprint"
        ),
        UniqueConstraint("critic_agent_run_id", name="uq_evaluations_critic_agent_run"),
        ForeignKeyConstraint(
            ["prompt_version_id", "workflow_run_id"],
            ["prompt_versions.id", "prompt_versions.workflow_run_id"],
            name="fk_evaluations_version_workflow_prompt_versions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["critic_agent_run_id", "workflow_run_id"],
            ["agent_runs.id", "agent_runs.workflow_run_id"],
            name="fk_evaluations_critic_workflow_agent_runs",
            ondelete="RESTRICT",
        ),
        Index("ix_evaluations_prompt_version_id", "prompt_version_id"),
        CheckConstraint("evaluation_round IN (1, 2, 3)", name="evaluation_round_range"),
        CheckConstraint("length(input_fingerprint) = 64", name="fingerprint_length"),
        CheckConstraint(
            "evaluated_target_mode IN ('generic', 'openai', 'anthropic', 'google')",
            name="target_mode_valid",
        ),
        CheckConstraint(
            "total_score IS NULL OR (total_score >= 0 AND total_score <= 100)",
            name="total_score_range",
        ),
        CheckConstraint(
            "(quality_status = 'unassessable' AND total_score IS NULL AND passed = false) OR "
            "(quality_status = 'qualified' AND total_score IS NOT NULL AND passed = true) OR "
            "(quality_status = 'not_qualified' AND total_score IS NOT NULL AND passed = false)",
            name="quality_state_valid",
        ),
    )

    id: Mapped[UuidPrimaryKey]
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    critic_agent_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    evaluation_round: Mapped[int] = mapped_column(Integer, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_target_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_renderer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_renderer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False)
    assessment_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    assessment: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    gate_results: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[CreatedAtColumn]

    workflow_run: Mapped[WorkflowRun] = relationship(
        "WorkflowRun", back_populates="evaluations", foreign_keys=[workflow_run_id]
    )
    prompt_version: Mapped[PromptVersion] = relationship(
        "PromptVersion", back_populates="evaluations", foreign_keys=[prompt_version_id]
    )
    critic_agent_run: Mapped[AgentRun] = relationship(
        "AgentRun", back_populates="evaluation", foreign_keys=[critic_agent_run_id]
    )
