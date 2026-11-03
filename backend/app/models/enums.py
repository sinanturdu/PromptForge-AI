"""Domain enumerations and schema constants shared across ORM models."""

from enum import StrEnum
from typing import Final

MAX_EVALUATION_ROUNDS_HARD_LIMIT: Final[int] = 3


class TargetMode(StrEnum):
    """Supported Prompt Compiler target modes (`ADR-011`)."""

    GENERIC = "generic"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class PromptDetailLevel(StrEnum):
    """Supported prompt detail levels (`ADR-012`)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ResponseDetailPreference(StrEnum):
    """Supported downstream response verbosity preferences (`ADR-012`)."""

    MINIMAL = "minimal"
    BALANCED = "balanced"
    COMPREHENSIVE = "comprehensive"


class ReasoningPreference(StrEnum):
    """Supported reasoning depth preferences (`ADR-012`)."""

    AUTO = "auto"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class WorkflowRunStatus(StrEnum):
    """Lifecycle states of an optimization workflow run."""

    PENDING = "PENDING"
    GENERATING = "GENERATING"
    EVALUATING = "EVALUATING"
    REVISING = "REVISING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PromptVersionSource(StrEnum):
    """Origin of a persisted prompt version."""

    ENGINEER_CREATE = "engineer_create"
    ENGINEER_REVISE = "engineer_revise"
    USER_EDIT = "user_edit"


class AgentRunStatus(StrEnum):
    """Execution states of an individual agent invocation."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
