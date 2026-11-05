"""Optional I/O seam for the existing optimizer loop, not a second policy engine."""

from typing import Protocol

from app.agents.critic_schemas import CriticResult
from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.agents.revision_schemas import RevisionInput, RevisionResult
from app.modules.prompts.architect_contracts import (
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
)
from app.modules.prompts.architect_critic_pass import SinglePassOutcome
from app.modules.prompts.optimization_contracts import EvaluationHistory


class OptimizationExecution(Protocol):
    async def initial(
        self,
        architect: PromptArchitectAgent,
        critic: PromptCriticAgent,
        source: ArchitectRequestSnapshot,
        context: ArchitectRunContext,
        request_id: str | None,
    ) -> SinglePassOutcome: ...

    async def revise(
        self, architect: PromptArchitectAgent, payload: RevisionInput, request_id: str | None
    ) -> tuple[RevisionInput, RevisionResult]: ...

    async def stage_revision(self, revised: RevisionResult) -> None: ...

    async def evaluate(
        self, critic: PromptCriticAgent, candidate: CandidateReady, request_id: str | None
    ) -> CriticResult: ...

    async def publish(self, history: EvaluationHistory) -> EvaluationHistory: ...
