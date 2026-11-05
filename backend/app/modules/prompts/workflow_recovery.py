"""Read-only recovery assessment: database evidence is never permission to replay."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.db.base import utc_now
from app.modules.prompts.workflow_persistence_contracts import PersistenceError
from app.modules.prompts.workflow_read_contracts import CommittedProof, RecoveryAssessment
from app.modules.prompts.workflow_readback import WorkflowReadbackService


class WorkflowRecoveryAssessmentService:
    def __init__(
        self, readback: WorkflowReadbackService, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._readback, self._clock = readback, clock

    async def assess(
        self, workflow_id: UUID, *, prompt_request_id: UUID | None = None
    ) -> RecoveryAssessment:
        summary = await self._readback.read(workflow_id, prompt_request_id=prompt_request_id)
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise PersistenceError("invalid")
        now = now.astimezone(UTC)
        row = summary.workflow
        expired = None if row.lease_expires_at is None else row.lease_expires_at <= now
        unresolved = tuple(value.id for value in summary.operations if value.unresolved)
        proofs = (
            tuple(
                CommittedProof(kind="operation_result", record_id=value.id)
                for value in summary.operations
                if value.status in {"COMPLETED", "FAILED"} and value.result_kind is not None
            )
            if not summary.findings
            else ()
        )
        if summary.history_valid and not summary.findings:
            proofs = (
                *proofs,
                *(
                    CommittedProof(
                        kind="evaluation",
                        record_id=value.id,
                        prompt_version_id=value.prompt_version_id,
                    )
                    for value in summary.evaluations
                ),
            )
        conditions = []
        if summary.findings:
            conditions.append("integrity_inconsistency")
            disposition = "integrity_investigation_required"
        elif row.status == "COMPLETED":
            conditions.append("confirmed_completed")
            disposition = "no_action_required"
        elif row.status in {"FAILED", "CLARIFICATION_REQUIRED"}:
            conditions.append(
                "terminal_failed" if row.status == "FAILED" else "terminal_clarification"
            )
            disposition = "manual_review_required"
        elif row.status == "PENDING":
            conditions.append("pending")
            disposition = "no_action_required"
        elif expired is False:
            conditions.append("active_with_valid_lease")
            disposition = "currently_active_do_not_interfere"
        else:
            conditions.append("expired_lease")
            disposition = (
                "inspect_operation_outcome"
                if unresolved
                else "reconcile_committed_identity"
                if proofs
                else "manual_review_required"
            )
        if unresolved:
            conditions.append("unresolved_operation")
        if row.status in {"COMPLETED", "FAILED", "CLARIFICATION_REQUIRED"} and not summary.findings:
            proofs = (*proofs, CommittedProof(kind="terminal", record_id=row.id))
        if proofs:
            conditions.append("committed_results_available")
        return RecoveryAssessment.model_validate(
            dict(
                workflow_run_id=row.id,
                prompt_request_id=row.prompt_request_id,
                observed_at=now,
                persisted_status=row.status,
                completed_rounds=row.completed_rounds,
                lease_expires_at=row.lease_expires_at,
                lease_expired=expired,
                conditions=tuple(conditions),
                disposition=disposition,
                committed=proofs,
                unresolved_operation_ids=unresolved,
                selected=summary.selected,
                findings=summary.findings,
                committed_result_reconciliation_available=bool(proofs),
            )
        )
