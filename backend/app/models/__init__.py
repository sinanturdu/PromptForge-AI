"""ORM model registry ensuring SQLAlchemy metadata discovery for Alembic."""

from app.models.agent_run import AgentRun
from app.models.enums import (
    MAX_EVALUATION_ROUNDS_HARD_LIMIT,
    AgentRunStatus,
    PromptDetailLevel,
    PromptVersionSource,
    ReasoningPreference,
    ResponseDetailPreference,
    TargetMode,
    WorkflowRunStatus,
)
from app.models.prompt_request import PromptRequest
from app.models.prompt_version import PromptVersion
from app.models.workflow_run import WorkflowRun

__all__ = [
    "MAX_EVALUATION_ROUNDS_HARD_LIMIT",
    "AgentRun",
    "AgentRunStatus",
    "PromptDetailLevel",
    "PromptRequest",
    "PromptVersion",
    "PromptVersionSource",
    "ReasoningPreference",
    "ResponseDetailPreference",
    "TargetMode",
    "WorkflowRun",
    "WorkflowRunStatus",
]
