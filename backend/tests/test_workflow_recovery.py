"""Recovery diagnostics and read-only exact-result reconciliation, never execution."""

from datetime import UTC, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.db.write_transactions import write_transaction
from app.llm.providers.fake import FakeLLMProvider
from app.models import AgentRun, WorkflowRun
from app.modules.prompts.persistence_contracts import (
    CallMetadataStorage,
    OperationBindingStorage,
    OperationResultStorage,
)
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    OperationParameters,
    PersistenceError,
    WorkflowFinalization,
)
from app.modules.prompts.workflow_read_contracts import OperationExpectation
from app.modules.prompts.workflow_recovery import WorkflowRecoveryAssessmentService
from workflow_read_fixtures import (
    NOTE,
    NOW,
    PRIVATE,
    QUESTION,
    SECRET_ID,
    Environment,
    PausingFake,
    completed,
    dump,
    execute,
    interrupted,
    new_workflow,
    script,
)
from workflow_read_fixtures import db as db


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def expected_operation(db: Environment, index: int = 0) -> OperationExpectation:
    reserved = db.reservations[index]
    async with write_transaction(db.writer) as session:
        agent = await session.get(AgentRun, reserved.agent.id)
        assert agent is not None
        parameters = OperationParameters.from_json_value(agent.parameters)
        return OperationExpectation.model_validate(
            dict(
                fence=reserved.workflow.fence(),
                agent_run_id=agent.id,
                identity=dict(
                    operation_key=agent.operation_key,
                    agent_name=agent.agent_name,
                    agent_version=agent.agent_version,
                    instruction_version=agent.instruction_version,
                    provider=agent.provider,
                    settings=parameters.settings,
                ),
                binding=OperationBindingStorage.from_json_value(agent.binding_data),
                parameters=parameters,
                result=None
                if agent.result_data is None
                else OperationResultStorage.from_json_value(agent.result_data),
                metadata=None
                if agent.call_metadata is None
                else CallMetadataStorage.from_json_value(agent.call_metadata),
            )
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profiles,decision,condition",
    [
        (["qualified"], None, "confirmed_completed"),
        (["critical"], "clarify", "terminal_clarification"),
        ([], "clarify", "terminal_clarification"),
    ],
)
async def test_confirmed_terminal_records_have_durable_proofs(
    db: Environment, profiles: list[str], decision: str | None, condition: str
) -> None:
    result = await completed(db, profiles, decision=decision)
    before = dump(db)
    assessment = await db.recovery.assess(result.terminal.workflow.id)
    assert assessment.findings == () and condition in assessment.conditions
    assert assessment.completed_rounds == len(profiles)
    assert any(
        value.kind == "terminal" and value.record_id == result.terminal.workflow.id
        for value in assessment.committed
    )
    assert len([value for value in assessment.committed if value.kind == "evaluation"]) == len(
        profiles
    )
    assert assessment.committed_result_reconciliation_available
    assert assessment.caller_acknowledgment == "unknown"
    assert not assessment.automatic_provider_replay_safe and not assessment.authorizes_mutation
    assert assessment.external_operation_outcome == assessment.former_worker_stopped == "unknown"
    for private in (
        PRIVATE,
        NOTE,
        QUESTION,
        SECRET_ID,
        str(result.terminal.workflow.execution_token),
        "PRIVATE_CRITIC",
        "PRIVATE_APPROVED_FEEDBACK",
    ):
        assert private not in assessment.model_dump_json() and private not in repr(assessment)
    assert dump(db) == before


@pytest.mark.anyio
async def test_terminal_failure_keeps_prior_confirmed_evaluations(db: Environment) -> None:
    from app.llm.errors import LLMErrorCode

    state = await new_workflow(db)
    provider = PausingFake([*script(["critical"]), LLMErrorCode.PROVIDER_UNAVAILABLE])
    try:
        result = await execute(db, state, provider)
    finally:
        await provider.aclose()
    assessment = await db.recovery.assess(state.id)
    assert assessment.findings == () and "terminal_failed" in assessment.conditions
    assert assessment.completed_rounds == 1 and assessment.selected is not None
    assert assessment.selected.prompt_version_id == result.terminal.best_version_id
    assert assessment.disposition == "manual_review_required"


@pytest.mark.anyio
async def test_pending_workflow_has_no_claim_or_execution_inference(db: Environment) -> None:
    state = await new_workflow(db)
    result = await db.recovery.assess(state.id)
    assert result.conditions == ("pending",) and result.disposition == "no_action_required"
    assert result.lease_expired is None and result.committed == ()
    assert result.unresolved_operation_ids == () and not result.authorizes_mutation


@pytest.mark.anyio
@pytest.mark.parametrize("call", [1, 2, 3, 4])
async def test_valid_and_expired_leases_do_not_authorize_repeating_unresolved_operations(
    db: Environment, call: int
) -> None:
    state = await interrupted(db, call)
    before = dump(db)
    live = await db.recovery.assess(state.id)
    assert live.findings == () and "active_with_valid_lease" in live.conditions
    assert "unresolved_operation" in live.conditions and len(live.unresolved_operation_ids) == 1
    assert live.disposition == "currently_active_do_not_interfere"
    db.clock.now += timedelta(seconds=30)
    expired = await db.recovery.assess(state.id)
    assert expired.findings == () and "expired_lease" in expired.conditions
    assert expired.disposition == "inspect_operation_outcome"
    assert expired.former_worker_stopped == "unknown" and not expired.automatic_provider_replay_safe
    assert expired.completed_rounds == (call - 1) // 2
    assert dump(db) == before
    expectation = await expected_operation(db, -1)
    reconciled = await db.readback.reconcile_operation(expectation)
    assert reconciled.status == "reserved_unresolved" and not reconciled.authorizes_mutation


@pytest.mark.anyio
async def test_committed_evaluation_without_terminal_acknowledgment_is_reconcilable(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_finalize(_command: WorkflowFinalization) -> Any:
        raise PersistenceError("unavailable")

    monkeypatch.setattr(db.persistence, "finalize_workflow", fail_finalize)
    state = await new_workflow(db)
    provider = PausingFake(script(["qualified"]))
    with pytest.raises(PersistenceError):
        await execute(db, state, provider)
    await provider.aclose()
    command = db.evaluations[0]
    before = dump(db)
    live = await db.recovery.assess(state.id)
    assert live.findings == () and live.disposition == "currently_active_do_not_interfere"
    assert "committed_results_available" in live.conditions
    db.clock.now += timedelta(seconds=31)
    expired = await db.recovery.assess(state.id)
    assert expired.disposition == "reconcile_committed_identity" and expired.lease_expired
    receipt = await db.readback.reconcile_evaluation(command)
    assert receipt.status == "exact_committed" and receipt.progress_advanced
    assert receipt.evaluation_id is not None and receipt.prompt_version_id is not None
    assert receipt.completed_rounds == 1 and receipt.terminal_status is None
    assert receipt.caller_acknowledgment == "unknown" and not receipt.authorizes_mutation
    assert provider.calls == 2 and dump(db) == before


@pytest.mark.anyio
async def test_computed_critic_result_without_commit_is_still_external_uncertainty(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[EvaluationCompletion] = []

    async def uncommitted(command: EvaluationCompletion) -> Any:
        commands.append(command)
        raise PersistenceError("unavailable")

    monkeypatch.setattr(db.persistence, "complete_evaluation", uncommitted)
    state = await new_workflow(db)
    provider = PausingFake(script(["qualified"]))
    with pytest.raises(PersistenceError):
        await execute(db, state, provider)
    await provider.aclose()
    before = dump(db)
    receipt = await db.readback.reconcile_evaluation(commands[0])
    assert receipt.status == "reserved_unresolved" and receipt.evaluation_id is None
    assert receipt.prompt_version_id is None and receipt.completed_rounds == 0
    assert not receipt.progress_advanced and receipt.caller_acknowledgment == "unknown"
    assert dump(db) == before and provider.calls == 2


@pytest.mark.anyio
async def test_exact_operation_reconciliation_requires_result_and_metadata(db: Environment) -> None:
    result = await completed(db, ["qualified"])
    expected = await expected_operation(db)
    before = dump(db)
    exact = await db.readback.reconcile_operation(expected)
    assert exact.status == "exact_committed" and exact.agent_run_id == expected.agent_run_id
    assert exact.prompt_version_id == result.evaluations[0].prompt_version_id
    identity_only = await db.readback.reconcile_operation(
        expected.model_copy(update={"result": None})
    )
    assert identity_only.status == "committed_identity_only"
    conflict = await db.readback.reconcile_operation(expected.model_copy(update={"metadata": None}))
    assert conflict.status == "conflict"
    wrong = expected.model_copy(
        update={"identity": expected.identity.model_copy(update={"operation_key": "different-key"})}
    )
    assert (await db.readback.reconcile_operation(wrong)).status == "conflict"
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "token",
        "revision",
        "count",
        "operation",
        "creator",
        "critic",
        "request",
        "workflow",
        "assessment",
        "metadata",
    ],
)
async def test_evaluation_reconciliation_does_not_accept_matching_key_alone(
    db: Environment, mutation: str
) -> None:
    result = await completed(db, ["qualified"])
    command = db.evaluations[0]
    if mutation == "token":
        command = command.model_copy(
            update={"fence": command.fence.model_copy(update={"execution_token": uuid4()})}
        )
    elif mutation == "revision":
        command = command.model_copy(
            update={"fence": command.fence.model_copy(update={"state_revision": 999})}
        )
    elif mutation == "count":
        command = command.model_copy(
            update={"fence": command.fence.model_copy(update={"completed_rounds": 1})}
        )
    elif mutation == "operation":
        command = command.model_copy(update={"critic_operation_key": "different-key"})
    elif mutation == "creator":
        command = command.model_copy(update={"architect_agent_run_id": uuid4()})
    elif mutation == "critic":
        command = command.model_copy(update={"critic_agent_run_id": uuid4()})
    elif mutation == "request":
        command = command.model_copy(
            update={"fence": command.fence.model_copy(update={"prompt_request_id": uuid4()})}
        )
    elif mutation == "workflow":
        command = command.model_copy(
            update={"fence": command.fence.model_copy(update={"workflow_run_id": uuid4()})}
        )
    elif mutation == "assessment":
        changed = command.evaluated.critic.model_copy(
            update={
                "output": command.evaluated.critic.output.model_copy(
                    update={"summary": "Different valid summary"}
                )
            }
        )
        command = command.model_copy(
            update={"evaluated": command.evaluated.model_copy(update={"critic": changed})}
        )
    else:
        changed = command.evaluated.critic.model_copy(
            update={
                "metadata": command.evaluated.critic.metadata.model_copy(
                    update={"duration_ms": 999.0}
                )
            }
        )
        command = command.model_copy(
            update={"evaluated": command.evaluated.model_copy(update={"critic": changed})}
        )
    before = dump(db)
    receipt = await db.readback.reconcile_evaluation(command)
    assert receipt.status in {"conflict", "absent"} and not receipt.authorizes_mutation
    assert receipt.evaluation_id is None and receipt.prompt_version_id is None
    assert dump(db) == before
    assert (await db.readback.read(result.terminal.workflow.id)).findings == ()


@pytest.mark.anyio
async def test_expired_historical_token_reads_exact_completion_and_terminal_without_mutating(
    db: Environment,
) -> None:
    result = await completed(db, ["qualified"])
    db.clock.now += timedelta(days=1)
    before = dump(db)
    evaluation = await db.readback.reconcile_evaluation(db.evaluations[0])
    terminal = await db.readback.reconcile_terminal(db.finalizations[-1])
    assert evaluation.status == terminal.status == "exact_committed"
    assert evaluation.evaluation_id == result.evaluations[0].evaluation_id
    assert terminal.terminal_status == "COMPLETED"
    assert not terminal.authorizes_mutation and dump(db) == before
    expected = db.finalizations[-1]
    wrong = expected.model_copy(update={"agent_run_id": uuid4()})
    assert (await db.readback.reconcile_terminal(wrong)).status == "conflict"


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["details", "token", "request"])
async def test_terminal_reconciliation_rejects_conflicting_claim_or_result(
    db: Environment, mutation: str
) -> None:
    await completed(db, ["qualified"])
    command = db.finalizations[-1]
    if mutation == "details":
        command = command.model_copy(
            update={
                "details": command.details.model_copy(
                    update={"termination_reason": "evaluation_limit"}
                )
            }
        )
    elif mutation == "token":
        command = command.model_copy(
            update={"fence": command.fence.model_copy(update={"execution_token": uuid4()})}
        )
    else:
        command = command.model_copy(
            update={"fence": command.fence.model_copy(update={"prompt_request_id": uuid4()})}
        )
    assert (await db.readback.reconcile_terminal(command)).status in {"conflict", "absent"}


@pytest.mark.anyio
async def test_integrity_inconsistency_prevents_committed_result_confirmation(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await completed(db, ["qualified"])

    async def no_provider_calls(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Read-only recovery must not call a provider")

    monkeypatch.setattr(FakeLLMProvider, "generate", no_provider_calls)
    evaluation_id = result.evaluations[0].evaluation_id
    version_id = result.evaluations[0].prompt_version_id
    valid_before = dump(db)
    valid_readback = await db.readback.read(result.terminal.workflow.id)
    assert not valid_readback.findings and valid_readback.history_valid
    assert [value.id for value in valid_readback.evaluations] == [evaluation_id]
    valid_assessment = await db.recovery.assess(result.terminal.workflow.id)
    assert valid_assessment.committed_result_reconciliation_available
    assert any(
        value.kind == "evaluation"
        and value.record_id == evaluation_id
        and value.prompt_version_id == version_id
        for value in valid_assessment.committed
    )
    assert (await db.readback.reconcile_evaluation(db.evaluations[0])).status == "exact_committed"
    assert dump(db) == valid_before

    async with write_transaction(db.writer) as session:
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == result.terminal.workflow.id)
            .values(final_version_id=None)
        )
    before = dump(db)
    readback = await db.readback.read(result.terminal.workflow.id)
    assert readback.findings and readback.history_valid
    assert [value.id for value in readback.evaluations] == [evaluation_id]
    assert readback.evaluations[0].prompt_version_id == version_id
    assessment = await db.recovery.assess(result.terminal.workflow.id)
    assert assessment.findings == readback.findings
    assert "integrity_inconsistency" in assessment.conditions
    assert assessment.disposition == "integrity_investigation_required"
    assert not assessment.committed_result_reconciliation_available
    assert assessment.committed == ()
    assert "committed_results_available" not in assessment.conditions
    assert not assessment.automatic_provider_replay_safe
    assert not assessment.authorizes_mutation
    assert (
        await db.readback.reconcile_evaluation(db.evaluations[0])
    ).status == "integrity_inconsistent"
    assert (
        await db.readback.reconcile_terminal(db.finalizations[-1])
    ).status == "integrity_inconsistent"
    assert dump(db) == before


@pytest.mark.anyio
async def test_clock_is_aware_utc_and_does_not_override_lease_meaning(db: Environment) -> None:
    state = await interrupted(db, 1)
    local = (NOW + timedelta(seconds=30)).astimezone(timezone(timedelta(hours=3)))
    recovery = WorkflowRecoveryAssessmentService(db.readback, clock=lambda: local)
    result = await recovery.assess(state.id)
    assert result.observed_at.tzinfo is UTC and result.lease_expired
    naive = WorkflowRecoveryAssessmentService(db.readback, clock=lambda: NOW.replace(tzinfo=None))
    with pytest.raises(PersistenceError):
        await naive.assess(state.id)


@pytest.mark.anyio
async def test_old_completion_identity_reports_current_selection_without_rewriting(
    db: Environment,
) -> None:
    result = await completed(db, ["bad", "critical", "qualified"])
    before = dump(db)
    receipt = await db.readback.reconcile_evaluation(db.evaluations[0])
    assert receipt.status == "exact_committed" and receipt.completed_rounds == 3
    assert receipt.evaluation_id == result.evaluations[0].evaluation_id
    assert receipt.prompt_version_id == result.evaluations[0].prompt_version_id
    assert (
        receipt.selected is not None
        and receipt.selected.prompt_version_id == result.evaluations[2].prompt_version_id
    )
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, 1])
async def test_terminal_clarification_reconciliation_matches_private_questions(
    db: Environment, count: int
) -> None:
    result = await completed(db, ["critical"] * count, decision="clarify")
    exact = await db.readback.reconcile_terminal(db.finalizations[-1])
    assert exact.status == "exact_committed" and exact.terminal_status == "CLARIFICATION_REQUIRED"
    command = db.finalizations[-1]
    changed = command.model_copy(
        update={
            "details": command.details.model_copy(
                update={"questions": ("Another relevant question?",)}
            )
        }
    )
    assert (await db.readback.reconcile_terminal(changed)).status == "conflict"
    assert QUESTION not in exact.model_dump_json()
    assert result.terminal.workflow.completed_rounds == count


@pytest.mark.anyio
async def test_invalid_expected_result_has_safe_diagnostics(
    db: Environment, caplog: pytest.LogCaptureFixture
) -> None:
    import traceback

    await completed(db, ["qualified"])
    command = db.evaluations[0].model_copy(update={"evaluated": {"private": PRIVATE}})
    with pytest.raises(PersistenceError) as error:
        await db.readback.reconcile_evaluation(command)
    assert error.value.code == "invalid" and error.value.__context__ is None
    assert (
        PRIVATE not in "".join(traceback.format_exception(error.value))
        and PRIVATE not in caplog.text
    )
