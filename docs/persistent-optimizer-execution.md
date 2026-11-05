# Persistent optimizer execution — M5E-4

## Inspected M5D sequence

Before the persistence adapter, `optimize_prompt()` performs one initial
`run_architect_critic_pass()`: `create_architect_handoff()` invokes Architect once,
returns clarification immediately or builds a blind CriticInput, then validates and
scores the independent Critic result as EvaluatedCandidate. A complete
EvaluationRound/History is validated before it becomes visible to the loop.

The existing loop selects the best evaluated candidate, stops on qualification or
the configured limit, then asks `check_revision_progress()` whether another attempt
is useful. It builds revision input from that selected baseline and records the
attempt key before dispatch. The returned revision is validated against its actual
input. `check_candidate_progress()` handles unchanged, clarification and duplicate
candidate stops before another Critic call. Otherwise the blind Critic evaluates
the new candidate and a validated round is appended. Only completed evaluations
consume the one-to-three-round budget. The existing selector, scorer, feedback
projection, protected spans and progress policies are the authorities throughout.

Expected LLM/validation/deadline failures return retained completed history; external
cancellation and unexpected errors propagate. Dependencies remain caller-owned.

## Integration compatibility requirement

M5D originally assembled contexts before any database reservation and published
validated history immediately in memory. M5E requires actual reserved AgentRun IDs
before dispatch and durable Evaluation commit before progress. A narrow optional
execution boundary is needed at initial-pass assembly, revision context assembly,
Architect staging, blind Critic dispatch and history publication. It must keep the
same optimizer loop and leave the default nonpersistent path unchanged.

M5E-3B also terminalized clarification during staging, before M5D's final deadline
checks. `complete_architect(..., defer_clarification=True)` is the coordinator's
backward-compatible adaptation: stage validated questions/metadata without a terminal
workflow transition, then use M5E-3C finalization after the loop decides its outcome.
Existing callers retain the default immediate-clarification behavior.

## Implemented internal entry point

`execute_persisted_optimizer(persistence, architect, critic, *, prompt_request_id,
workflow_run_id, identity, expected_revision=0, lease_seconds=300, request_id=None)`
claims the explicitly selected PENDING workflow. It never creates one. A rejected,
already claimed or terminal workflow cannot initiate a provider call. Retrying this
entry point is not resumption. Source, preferences, maximum evaluations, protected
spans, timeout and policy versions come from the actual immutable stored binding,
through `execution_snapshot()` after a successful claim. Callers cannot override the
optimization configuration at execution time.

`PersistentExecutionIdentity` declares known requested Architect/Critic provider
and ModelSettings from caller composition. It does not select different settings
inside an injected agent or invent observed telemetry. Stable operation keys are
`create:1`, `revise:2/3`, and `evaluate:1/2/3`, scoped to this workflow. Actual agent
and instruction versions come from the existing version constants.

The return value, `PersistentOptimizationResult`, contains the existing validated
OptimizationOutcome, durable FinalizationReceipt and bounded round-to-version/
Evaluation references. It is returned only after terminal persistence succeeds.
All these are detached Pydantic values; full serialization remains sensitive.
The caller owns agents, gateway/provider lifetime, persistence service and engine
disposal. The coordinator does not import ORM/session APIs.

The original `optimize_prompt()` default remains nonpersistent. Its optional
`OptimizationExecution` seam has no selection, score, stop, retry or loop algorithm:
it supplies I/O at the existing decision boundaries. The persistent adapter invokes
the same M5D dispatch guards, handoff, blind-input builder, revision validation,
scoring and loop. It does not implement a second optimizer or workflow framework.

## Durable dispatch sequence

| M5D phase | Persistence action and ordering |
| --- | --- |
| Initial create | Renew live claim, reserve Architect create, close transaction, call existing handoff/agent, stage validated result. |
| Candidate assessment | After staging commits, renew/reserve independent Critic bound to actual creator/fingerprint; close transaction; call only with existing blind CriticInput. |
| Completed evaluation | Validate/scored EvaluatedCandidate and proposed history, atomically complete via M5E-3C, then read validated committed history before publishing it to the loop. |
| Revision | Resolve the selected baseline's persisted Evaluation/version from committed history; reserve with existing feedback digest/attempt binding; assemble actual reserved AgentRun context; dispatch, validate and stage. |
| Terminal decision | Convert existing outcome to TerminalDetailsStorage and invoke fenced M5E-3C finalization; return only its committed receipt. |

All database work uses established short service-owned `write_transaction()` units.
`execution_snapshot()` is a bounded internal source/history read using the same
dedicated engine, not a public readback API. It revalidates source, history, current
fence/progress and best selection, returning at most three Evaluation references.
No service connection/session escapes any call. Tests acquire an independent
BEGIN IMMEDIATE writer during every provider dispatch and inspect that the actual
AgentRun is reserved and Architect staging is committed before Critic invocation.

Only committed valid Evaluations enter the round budget. A generated candidate,
provider retry/repair, failed dispatch, unchanged revision or clarification creates
no substitute Evaluation. Gateway retry/repair semantics and prompts are unchanged.
Observed metadata is the existing explicit allowlist. Failure metadata is retained
when returned by the gateway; unavailable observations stay null. No elapsed time,
token counts or response bodies are manufactured by the adapter.

The loop consumes committed history. Actual persisted baseline references are
resolved by matching the selected candidate and Critic assessment; the existing
provenance service validates their feedback projection. M5E-3C assigns the parent
version from that baseline Evaluation. A nonlatest better candidate stays selected.
With unchanged feedback/protected spans, attempting its revision again repeats the
existing M5D attempt key and stops. A third revision from that nonlatest baseline
cannot be manufactured just to exercise lineage; the integration tests instead
verify the correct earlier selection and absence of an extra paid dispatch.

## Leases, failures and replay

`renew_claim(fence, lease_seconds=...)` is a separate short transaction before each
operation reservation. It requires a current token, exact revision/count, active
phase and strictly unexpired claim. It increments state_revision and extends the
lease without shortening it; duration is bounded to 1–900 seconds. It never rotates
tokens, revives expired claims, takes ownership or runs a background heartbeat.
Reservation and result writes remain independently fenced.

A single provider call must finish within that renewed lease. Expiration during a
call rejects its result and stops execution, even if generation succeeded. There is
no renewal after expiry, detached work, implicit retry or status fabricated under
lost authority. Multi-host clocks, takeover and long-call renewal strategy remain
deployment/recovery work. The stored optimizer deadline still limits the whole
M5D execution, including adapter I/O; initial claim/input loading precede that timer.

Expected provider/validation/deadline outcomes finalize FAILED while preserving
committed evaluations/best selection. A reserved failed operation is marked failed
atomically, or via the existing failure service when observed failure telemetry is
available, followed by terminal reconciliation. M5D's failed phase (e.g. validation)
stays in OptimizationOutcome; terminal details identify the actual failed AgentRun
operation when one exists. These are different scopes, not alternate failure policy.
Questions use CLARIFICATION_REQUIRED; normal technical completion uses COMPLETED
with qualified/not_qualified/unassessable quality kept separate.

If a deadline expires after a confirmed Evaluation commit but before in-memory
publication, finalization reloads the committed history and retains that evaluation
in the failed result. A confirmed clarification stage is similarly not terminal
until finalization, so staging cannot prematurely claim successful execution.

Completion/failure/finalization permit at most one additional exact persistence-only
attempt on `unavailable`. This reconciles a lost acknowledgment using stored identity
or safely persists the same already computed result after rollback. It never repeats
a provider. Claim, renewal and reservation are not retried: their ambiguity does not
authorize dispatch. A replayed reservation, even RUNNING, stops with conflict; it
does not prove whether the earlier model call happened. Conflicts/invalid data are
not retried. A persistent infrastructure failure propagates as a safe typed error,
never as successful optimization.

External cancellation propagates after service cleanup and stops further dispatch.
It deliberately does not attempt unbounded terminal writes from a cancelled task.
Cancellation at an unacknowledged persistence boundary, including optimizer deadline
cancellation, leaves the outcome uncertain and stops safely. A result may already
be durable; the adapter does not guess FAILED/COMPLETED or replay it automatically.
M5E-5 must inspect actual operation identity before any explicit recovery decision.
Previously committed history remains intact.

## Verification and remaining activation limits

The integration suite uses actual agents/gateway with RecordingFake/FakeLLMProvider
and temporary migrated WAL/FULL/FK SQLite files. It covers three-round execution,
all terminal categories, earlier-best and duplicate guards, immutable stored config,
blinded Critic input, truthful nullable telemetry, competing claims/reservations,
exact/conflicting replays, lost acknowledgments, failed/cancelled writes, lease
expiry/token replacement, deadline/commit races, independent version allocation and
the unchanged nonpersistent path. Concurrency uses events and bounded waits, without
sleeps. Existing M5D/M5E-3B/M5E-3C tests are unchanged.

Use the approved private Python environment described in
[the persistence guide](workflow-persistence-services.md), with actual SQLite 3.53.1
through sqlite3/aiosqlite. Run `-m pytest tests/test_persistent_optimizer.py -q`, the
existing optimizer/persistence suites, full backend pytest, strict mypy on
`app tests migrations benchmarks`, Ruff lint/format and Git whitespace checks.
Development DB size, timestamp, hash and WAL/SHM absence must remain unchanged.
Fake tests establish contract/sequencing behavior, not real-model quality, power-loss
durability or PostgreSQL concurrency. No schema/runtime/dependency migration is
required for this integration; the actual development DB is never migrated here.

This path is implemented internal functionality, explicitly invoked in the caller's
task. It is not wired into current APIs, startup, health checks or frontend. There
is no background runner, scheduler, recovery worker, automatic workflow replay or
provider ownership change. M5E-5 still owns interrupted-run inspection, durable
readback beyond this bounded execution input, explicit recovery/resumption policy
and reconciliation of unknown in-flight operations. PostgreSQL allocator locking,
deadlock policy and multi-host lease skew remain unqualified as documented in M5E-3C.

M5E-5 now implements independent [durable readback and recovery assessment](workflow-readback-recovery.md),
including exact read-only committed-identity reconciliation. The earlier deferral
above is historical: automatic recovery/resumption and unknown external-call replay
remain intentionally unimplemented. Read diagnostics confer no execution authority.
