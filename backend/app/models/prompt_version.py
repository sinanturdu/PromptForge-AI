"""ORM model for immutable canonical and target-rendered prompt versions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    JSON,
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

from app.db.base import Base, CreatedAtColumn, UuidPrimaryKey
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT, TargetMode

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.evaluation import Evaluation
    from app.models.prompt_request import PromptRequest
    from app.models.workflow_run import WorkflowRun


class PromptVersion(Base):
    """Stores a versioned CanonicalPrompt payload and its rendered prompt text."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "prompt_request_id",
            "version_number",
            name="uq_prompt_versions_request_version_number",
        ),
        UniqueConstraint("agent_run_id", name="uq_prompt_versions_agent_run_id"),
        UniqueConstraint("id", "workflow_run_id", name="uq_prompt_versions_id_workflow"),
        Index("ix_prompt_versions_prompt_request_id", "prompt_request_id"),
        Index("ix_prompt_versions_workflow_run_id", "workflow_run_id"),
        Index("ix_prompt_versions_parent_version_id", "parent_version_id"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(
            "evaluation_round IS NULL OR "
            f"(evaluation_round BETWEEN 1 AND {MAX_EVALUATION_ROUNDS_HARD_LIMIT})",
            name="evaluation_round_range",
        ),
        CheckConstraint(
            "source IN ('engineer_create', 'engineer_revise', 'user_edit')",
            name="source_valid",
        ),
        CheckConstraint(
            "target_mode IN ('generic', 'openai', 'anthropic', 'google')",
            name="target_mode_valid",
        ),
    )

    id: Mapped[UuidPrimaryKey]
    prompt_request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("prompt_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    parent_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    target_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TargetMode.GENERIC.value,
    )
    renderer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_prompt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rendered_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    change_summary: Mapped[list[str] | dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[CreatedAtColumn]

    prompt_request: Mapped[PromptRequest] = relationship(
        "PromptRequest",
        back_populates="prompt_versions",
        foreign_keys=[prompt_request_id],
    )
    workflow_run: Mapped[WorkflowRun | None] = relationship(
        "WorkflowRun",
        back_populates="prompt_versions",
        foreign_keys=[workflow_run_id],
    )
    parent_version: Mapped[PromptVersion | None] = relationship(
        "PromptVersion",
        remote_side="PromptVersion.id",
        back_populates="child_versions",
        foreign_keys=[parent_version_id],
    )
    child_versions: Mapped[list[PromptVersion]] = relationship(
        "PromptVersion",
        back_populates="parent_version",
        foreign_keys=[parent_version_id],
        passive_deletes=True,
    )
    agent_run: Mapped[AgentRun | None] = relationship(
        "AgentRun",
        back_populates="prompt_version",
        foreign_keys=[agent_run_id],
    )
    evaluations: Mapped[list[Evaluation]] = relationship(
        "Evaluation",
        back_populates="prompt_version",
        foreign_keys="Evaluation.prompt_version_id",
        passive_deletes=True,
    )
