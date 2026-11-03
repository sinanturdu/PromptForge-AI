"""ORM model for raw user prompt optimization requests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtColumn, UuidPrimaryKey
from app.models.enums import (
    PromptDetailLevel,
    ReasoningPreference,
    ResponseDetailPreference,
    TargetMode,
)

if TYPE_CHECKING:
    from app.models.prompt_version import PromptVersion
    from app.models.workflow_run import WorkflowRun


class PromptRequest(Base):
    """Represents a user-submitted raw prompt and optimization preferences."""

    __tablename__ = "prompt_requests"
    __table_args__ = (
        Index("ix_prompt_requests_created_at", "created_at"),
        CheckConstraint("length(trim(raw_prompt)) > 0", name="raw_prompt_not_empty"),
        CheckConstraint(
            "target_mode IN ('generic', 'openai', 'anthropic', 'google')",
            name="target_mode_valid",
        ),
        CheckConstraint(
            "prompt_detail_level IN ('low', 'medium', 'high')",
            name="prompt_detail_level_valid",
        ),
        CheckConstraint(
            "response_detail_preference IN ('minimal', 'balanced', 'comprehensive')",
            name="response_detail_preference_valid",
        ),
        CheckConstraint(
            "reasoning_preference IN ('auto', 'low', 'medium', 'high')",
            name="reasoning_preference_valid",
        ),
    )

    id: Mapped[UuidPrimaryKey]
    raw_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TargetMode.GENERIC.value,
    )
    task_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_detail_level: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PromptDetailLevel.MEDIUM.value,
    )
    response_detail_preference: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=ResponseDetailPreference.BALANCED.value,
    )
    reasoning_preference: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ReasoningPreference.AUTO.value,
    )
    created_at: Mapped[CreatedAtColumn]

    workflow_runs: Mapped[list[WorkflowRun]] = relationship(
        "WorkflowRun",
        back_populates="prompt_request",
        foreign_keys="WorkflowRun.prompt_request_id",
        order_by="WorkflowRun.created_at",
        passive_deletes=True,
    )
    prompt_versions: Mapped[list[PromptVersion]] = relationship(
        "PromptVersion",
        back_populates="prompt_request",
        foreign_keys="PromptVersion.prompt_request_id",
        order_by="PromptVersion.version_number",
        passive_deletes=True,
    )
