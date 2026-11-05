# Bounded in-memory optimization — M5D-4

## Entry point and ownership

```python
from app.modules.prompts.optimizer import optimize_prompt

outcome = await optimize_prompt(
    architect, critic, prompt_request,  # injected agents and existing PromptRequestRead
    max_evaluation_rounds=3,
    protected_spans=(),
    context=None,
    request_id=None,
    timeout_seconds=None,
)
```

The coordinator runs sequentially in the calling task. It owns only local history,
operation counters and an attempted-revision registry. It creates no background
tasks, providers, gateways, IDs, sessions or records, and closes none of the caller's
resources. There is no workflow retry or automatic replay. Gateway-managed retries,
request timeouts and structured-output repair remain unchanged.

`max_evaluation_rounds` must be an integer 1–3; booleans, floats and out-of-range
values are rejected, never clamped. Three is the default maximum, not a required
number of iterations. Invalid limits/deadlines raise a fixed
`LLMError(INVALID_REQUEST)` before dispatch. Invalid source/context/span data instead
returns a failed input-validation outcome with no completed history.

The current initial-pass function hides creation and evaluation behind one call.
Two private borrowed-agent adapters enforce dispatch caps/deadlines and identify
the operation that failed without changing that function's public contract. They
delegate to the injected agents; they are not a service layer or framework.

## Execution and round accounting

Before any await, the mutable request and supplied context/spans are snapshotted
and validated. Original content, preferences, identity and workflow ancestry remain
pinned. Individual span bounds/source/uniqueness are checked before generation;
the full existing revision binding, including collective protection rules, is
checked before revision. Initial rewrites must retain declared protected text;
initial preserve retains the original raw prompt exactly, following the existing
contract rather than inserting context into a preserved prompt.

1. Reuse `run_architect_critic_pass()` exactly once. Architect clarification returns
   immediately, with no Critic dispatch, score, history or selection.
2. Otherwise construct round 1 from its bound `EvaluatedCandidate`, then validate
   the complete `EvaluationHistory`. Publish the new history only after the whole
   append validates within the remaining budget.
3. Call the unchanged `select_best_candidate()` after every completed evaluation.
   Stop with `quality_passed` only when the selected `ScoringResult.passed` is true.
   A high total alone cannot bypass intent, severity or assessability gates.
4. Stop at the configured maximum; otherwise call `check_revision_progress()` with
   the entire history, pinned spans and this run's attempted keys.
5. Build `make_revision_input()` from the selected round's own candidate and Critic
   result. Check the advised baseline, next round and deterministic attempt key.
   Record the key before calling Architect.revise() once. Never call create() again.
6. Validate the exact revision result against the dispatched input, then call
   `check_candidate_progress()` before any further Critic dispatch.
7. For an eligible `RevisedCandidate`, use only
   `make_critic_input(revised.candidate) -> critic.evaluate() -> EvaluatedCandidate`.
   Append the completed round with the exact `revision` and selected baseline number.
   Repeat only while progress is justified and the limit has not been reached.

A proposed next number is not a started Critic operation, and a started Critic
operation is not a completed evaluation. `OptimizationCounts` records started
Architect creation, Architect revision and Critic evaluation operations. Only valid,
fully bound outcomes enter history. No fourth Critic dispatch can start, including
when a dependency fails. Clarification, unchanged text, duplicate text, failed
Critic results and gateway attempts never advance completed history.

The coordinator calls no alternative scorer. Existing contracts calculate each
new score and also recalculate existing arithmetic when validating nested history,
selection and progress. These consistency checks are not new model assessments.
The tests explicitly count both kinds:

| Normal stopping path | create / revise / Critic operations | Gateway operations | Completed evaluations | ScoringEngine calculations, including revalidation |
| --- | --- | ---: | ---: | ---: |
| Qualified round 1 | 1 / 0 / 1 | 2 | 1 | 7 |
| Qualified round 2 | 1 / 1 / 2 | 4 | 2 | 22 |
| Qualified round 3 | 1 / 2 / 3 | 6 | 3 | 43 |
| Unchanged revision after round 1 | 1 / 1 / 1 | 3 | 1 | 13 |

Gateway retries/JSON repair may increase provider attempts without increasing
these gateway-operation or completed-evaluation counts. The arithmetic-check
counts describe current validation boundaries, not additional quality judgments.

## Best baseline, provenance and independence

Selection policy 1.0.0 remains qualified first, no reported supported critical issue,
higher known total, higher known intent, then earlier round. Null stays unavailable;
there is no substitute zero or partial total. Best available is distinct from approved.
An unassessable candidate without a reported supported critical issue can still
outrank a known critical candidate; absence of a report does not prove safety.

Every orchestrated round after round 1 must include `EvaluationRound.revision`.
The optimizer rejects a bare `CandidateReady`. It deeply revalidates the returned
revision and checks its exact baseline, current context, proposed round, protected
spans and eligible issue keys against the actual dispatched input. Existing
validators check addressed-key claims, protected-text presence, canonical/rendered
agreement, request ancestry, versions and metadata. History then binds that same
candidate to its own independent Critic result, evidence, fingerprint and score.

The existing revision contract still lacks a complete originating-feedback digest.
The coordinator owns the actual local input/result sequence and uses all available
checks, but cannot authenticate imported history or prove historical feedback
identity from positional keys. No compatible-contract change or hash-as-credential
claim is introduced. Literal evidence attribution and protected-text presence do
not establish semantic fidelity, actual issue resolution or task success.

Every Critic receives only the existing blind `CriticInput`: unchanged source and
preferences, exact current rendered prompt, target and renderer. No explanations,
canonical organization, prior scores/findings, revisions, round numbers, history,
selection or operation telemetry enter that input. Revision uses only M5D-2's
approved supported-feedback projection; uncertain issues do not become requirements.

## Explicit outcomes and stops

`OptimizationOutcome` is strict, frozen and deeply revalidated. It contains execution
status, termination reason, optional completed history and selected existing round,
derived quality status, clarification questions, safe failure operation/code, bounded
dispatch counts, configured maximum, optimizer version and progress-policy version.
It does not duplicate candidate content or create a final prompt/version.

| Execution status | Reasons | Meaning |
| --- | --- | --- |
| `completed` | `quality_passed`, `evaluation_limit`, `no_actionable_feedback`, `unchanged_baseline`, `already_evaluated`, `repeated_revision_attempt` | Execution stopped normally. Selected quality can still be `not_qualified` or `unassessable`. |
| `clarification_required` | `clarification_required` | Focused questions; retain earlier completed history if any. No new candidate/version/evaluation. |
| `failed` | `provider_failure`, `validation_failure`, `deadline_exceeded` | Execution failed. Retain only previously completed history and its best selection. Quality is absent when no evaluation completed. |

Unchanged revisions preserve the exact existing baseline without another Critic call.
Repeated input fingerprints, including different canonical organizations with identical
rendering under the pinned source/target/renderer, stop before another Critic dispatch.
Text is not normalized; changed wording alone makes no semantic-improvement claim.

Attempt keys use the unchanged M5D-3 projection and ignore proposed round numbers and
current telemetry. If round 2 is worse and round 1 remains best, its same feedback
was already attempted: the workflow stops before repeating that revision, even with
one slot left. Three nonqualifying evaluations are possible when each prior revision
provides a new selected baseline. Earlier candidates are never discarded to force
another operation.

Failure operation identifies `input_validation`, `architect_create`,
`architect_revise`, `critic_evaluate`, `validation`, `selection` or `progress`.
The safe error code retains the existing `LLMErrorCode`, without messages, provider
bodies, credentials or exception objects. Invalid binding/evidence/provenance/scoring
is validation failure; expected provider errors remain execution failures. The
optimizer catches expected LLM/Pydantic failures, not arbitrary programming errors.
Unexpected errors propagate for the caller to handle safely; there is no replay.

## Deadline and cancellation

An optional positive finite timeout uses one absolute event-loop monotonic deadline
and `asyncio.timeout_at()` around the whole run in the current task. Budget checks
run before and after each agent dispatch and before publishing validated history or
returning success. Expiry during an awaited operation cancels that operation and
returns failed execution with only earlier completed history. An unrelated
`TimeoutError` is not silently reclassified as this timer's expiry.

External `asyncio.CancelledError` propagates unchanged, including its cancellation
message. No outcome is fabricated, no provider is closed, and no detached work is
created. Future persistence must separately record interruption per ADR-005. Async
deadlines depend on cooperative cancellation; synchronous work is bounded by current
input/history limits and checked when control returns. No polling or sleeps are added.

## Privacy, versioning and future persistence

Optimizer version **1.0.0** and progress policy **1.0.0** appear in the outcome;
selection policy **1.0.0** remains on the selection. Existing Architect creation,
revision and Critic instruction/agent versions, rubric **2.0.0**, scorer **1.0.0**,
renderer identity and fingerprint algorithm remain unchanged and validated.

The optimizer logs nothing. Ordinary representations hide history, selection and
questions. Full serialization and structured validation details contain private
source text/evidence/telemetry and must remain restricted. All text is untrusted
data. IDs/hashes do not prove authorization, authenticity or semantic correctness.

| Future record | Intended mapping and remaining work |
| --- | --- |
| Existing PromptRequest | Load/authorize the existing source and ownership; never invent a request or modify its preferences. Initial clarification association remains caller-owned. |
| WorkflowRun | Configuration/policy snapshot, actual dispatch/current-round state separately from `len(history.rounds)`, lifecycle and safe stop/error fields. Completed execution need not be qualified. Later failure is FAILED with retained best evaluation; no `COMPLETED/error_partial`. |
| Architect/Critic AgentRun | Actual create/revise/evaluate operations, versions and independent telemetry. Current IDs are assertions only; default revision context clears the prior Architect ID. Future persistence needs explicit per-operation ID binding and capture of failed/unchanged/clarification telemetry. |
| PromptVersion | Completed candidate canonical/rendered text, renderer/target, source `engineer_create` or `engineer_revise`, and parent version from `baseline_round_number`, which need not be the latest version. No version for clarification/unchanged/unevaluated output. |
| Evaluation (not implemented) | Exact candidate/Critic binding, rubric assessments/issues/evidence, null-aware scoring/gates and provenance. Final selection references an existing owned evaluated version. |

The outcome is not a durable execution journal or complete billing ledger. It retains
successful completed-round metadata and minimal dispatch counts, not every failed
or non-evaluated operation's full result/usage. A persistence milestone must add that
explicit capture boundary, concurrency-safe run/attempt reservation, version allocation,
idempotency, null-score storage and clarification/resume lifecycle design. Existing
WorkflowRun has no waiting-for-input state; none is invented here.

Use short transactions to authorize/load/reserve state, release sessions before LLM
awaits, then recheck ownership/state and atomically persist completed version,
evaluation, telemetry and counters. Final reference selection and failed/interrupted
bookkeeping require explicit transaction ownership. Repositories flush; services
commit/rollback. Nothing in M5D-4 writes any of these records or activates an API.

Run `uv run pytest tests/test_optimizer.py` and the full backend checks. Fake-provider
tests cover early stops, all bounds/gates, best-baseline feedback, provenance,
duplicates, partial failures, timer expiry, cancellation, retries/repair, precise
accounting, blind inputs, private diagnostics, immutable inputs and borrowed resources.
They establish orchestration behavior, not real-model quality, calibration, prompt
injection resistance or production readiness. Frontend/API/persistence remain unchanged.
