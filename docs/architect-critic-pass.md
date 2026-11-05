# Architect -> Critic single pass — M5C-4

## Opt-in in-memory execution

```python
from app.modules.prompts.architect_critic_pass import (
    EvaluatedCandidate,
    run_architect_critic_pass,
)

# saved_request is an existing PromptRequestRead loaded by the current service.
# The caller constructs/owns architect, critic, their gateways and providers.
outcome = await run_architect_critic_pass(
    architect,
    critic,
    saved_request,
    context=existing_architect_run_context,  # optional ArchitectRunContext
    request_id=correlation_id,              # optional diagnostic correlation
)
if isinstance(outcome, EvaluatedCandidate):
    copy_ready = outcome.candidate.rendered_prompt
    quality = outcome.scoring.status
else:
    questions = outcome.questions  # ClarificationRequired; no candidate or score
```

The coordinator calls the existing `create_architect_handoff()` exactly once.
Clarification returns its existing validated contract immediately. Otherwise
`make_critic_input()` derives blind input, `PromptCriticAgent.evaluate()` executes
once, and `EvaluatedCandidate` validation applies `ScoringEngine.score()` once
after binding the evaluation to the candidate. There is no pass retry, revision,
quality loop, final-version selection, workflow framework or pass-through service.

Normal candidate execution uses two gateway operations. Clarification uses one.
Gateway-managed transport retries/JSON repair may consume more provider attempts
within those operations; they do not create extra evaluations or workflow rounds.
The agents can borrow either separate gateways or the same stateless gateway with
different explicit model settings; each builds its own two-message conversation.
No startup agent, provider selection, public endpoint or frontend feature is added.
`POST /api/v1/prompt-requests` still only saves the request.

## Outcomes and separation

`SinglePassOutcome = ClarificationRequired | EvaluatedCandidate`.

| Outcome | Contents | Meaning |
| --- | --- | --- |
| ClarificationRequired | Existing original snapshot, context, questions, Architect versions/metadata | Blocking input needs resolution. No candidate, Critic result, score, version or final prompt. |
| EvaluatedCandidate | `status="evaluated"`, CandidateReady, CriticResult, ScoringResult | Execution completed. Quality remains separately qualified, not_qualified or unassessable. |

CandidateReady retains canonical/rendered text, source identity/ancestry, Architect
notes and Architect metadata. CriticResult retains its own findings, evidence,
versions and execution metadata. ScoringResult retains deterministic gates/total
and versions. No notes or telemetry are appended to copy-ready text or pooled into
an invented combined usage total. Unknown usage remains unknown.

Preserve is evaluated independently, including when it fails quality gates or is
unassessable; original text stays byte-for-byte equivalent as a Python string.
Neither not_qualified nor unassessable is an execution failure. `evaluated` is not
WorkflowRun COMPLETED, a persisted Evaluation, final-version selection or proof of
real-world prompt quality. Fake tests establish software behavior, not accuracy.

## Binding and validation

The existing handoff snapshots the mutable request before its first await, checks
local UUID ancestry and preserves original identity/preferences. Caller mutations
while an agent waits cannot relabel the frozen source. Context IDs are asserted
links, not proof of existence, authorization, run state or user ownership. The
current unauthenticated schema has no user-owner column.

The optional context is **ArchitectRunContext**: its agent_run_id refers only to
the Architect operation. It does not identify the Critic operation; no Critic run
ID or record is invented/reused. A future executor must bind its separate Critic
AgentRun explicitly when implementing persistence.

EvaluatedCandidate revalidates nested CandidateReady/CriticResult/ScoringResult
models rather than trusting frozen instances or model_copy(). It reuses the
existing decision, canonical/rendering, metadata and score invariants. Strict JSON
round trips preserve UUID, datetime, enum and Decimal parsing. The evaluated target,
renderer name/version and input fingerprint must match `make_critic_input(candidate)`.
The Critic's existing evidence-attribution validator is also reused on the returned
result before scoring, including when a dependency returned a tampered model.

`critic_input_fingerprint()` extracts the unchanged M5C-2 algorithm into the shared
input boundary: SHA-256 of sorted-key UTF-8 JSON, ensure_ascii=False, over the full
local CriticInput snapshot. This includes existing request identity/timestamp,
preferences, exact rendered text and target/renderer. The same helper is used by
the Critic and integration validation. It is neither authentication nor a safe log
identifier, and does not prove semantic correctness. No agent contract, policy,
instruction/rubric version or fingerprint serialization changes.

Scoring is derived inside evaluated-outcome validation, after binding succeeds.
The coordinator supplies candidate and Critic only. A supplied score during later
outcome revalidation must equal the deterministic derivation; malformed or unrelated
scores are rejected. This avoids duplicating scoring arithmetic or scoring twice
while constructing one pass outcome. Explicitly revalidating a saved outcome
recomputes local arithmetic once, without agents/provider calls. Current rubric and
scoring versions must agree with the evaluation. Historical rubric replay needs an
explicit later version-dispatch policy; unsupported versions fail today.

Blind Critic input contains only the approved original content/preferences, exact
candidate and target/renderer context. It excludes Architect decisions, changes,
questions, canonical internals, execution metadata, run history and previous scores.
Local identity/timestamps remain excluded from provider messages as before.

## Failures, privacy and ownership

- Invalid original input/ancestry fails through the existing handoff before generation.
- Architect gateway/structured-output errors propagate; Critic/scoring do not run.
- Clarification is returned without Critic/scoring, not replaced by a raw candidate.
- Critic gateway errors, malformed evidence and cancellation propagate; no partial
  evaluated outcome is returned. There is no automatic retry of the entire pass.
- Invalid binding, nested result data or scoring output raises the existing safe
  LLM error, using INVALID_STRUCTURED_OUTPUT for integration validation failures.
  Existing dependency LLM errors retain their category; no raw ValidationError
  context is attached. Programmer failures remain caller failures, not quality statuses.
- Neither cancellation nor failure closes borrowed providers or writes any record.
  Already-consumed attempts cannot be undone; future execution telemetry/recovery
  belongs to the executor, not a fabricated partial success here.

The coordinator emits no logs. Sensitive nested fields are hidden in its ordinary
representation. Explicit dumps still contain prompts, evidence and metadata and
must not be logged. Future executor/API error handling must keep arbitrary
dependency failures and persisted diagnostics content-free.

## Future M5D and persistence

M5D can call this boundary for initial creation, then own evaluation rounds, stop
conditions, clarification handling and best-version selection under ADR-003/007.
Architect revise() is available through the [M5D-2 revision boundary](architect-revision.md);
revision orchestration is not implemented. Do not simulate revision by repeatedly
calling initial create(), or treat transport repair as a round.
The three-round cap, stable rubric snapshot and best-version rules remain future
controller responsibilities. Qualification alone never allocates a final version.

| Future record | Intended mapping |
| --- | --- |
| Existing PromptRequest | Candidate/clarification original ID and unchanged request preferences. |
| WorkflowRun | Executor-owned lifecycle/configuration/round counters and eventual final selection. |
| Architect AgentRun | Existing context reference; Architect version/operation and its separate telemetry. |
| Critic AgentRun | Separate executor-owned record for evaluate(), with Critic versions/telemetry. |
| PromptVersion | Candidate canonical/rendered text, target/renderer, request/run binding; existing engineer_create source for initial preserve/rewrite. No version for clarification. |
| Evaluation (planned table) | Exact PromptVersion and Critic run, evaluated input/target/renderer, rubric, criterion assessments/issues and Python scoring/gates, including unassessable state. |

Use short transactions to load/check ancestry, authorization, state and idempotency,
then snapshot and release sessions before LLM waits. Recheck source/run state and
ownership in a fresh transaction before atomically storing the candidate/evaluation,
execution telemetry and state. Repositories flush; services commit/rollback;
DatabaseSessionManager does not auto-commit. Apply failure/cancellation bookkeeping
and interrupted-run recovery deliberately if future execution records exist.

Unresolved: concurrency-safe version allocation; separate Critic execution ancestry;
evaluation schema/idempotency; stale request completion; durable clarification/resume
(WorkflowRun has no waiting-for-input state); null-score/evidence/usage storage;
historical rubric replay and final-version eligibility. None is solved by local
UUID/hash checks. No sessions, records, migrations or evaluation persistence exist
in this boundary.

Future REA evidence belongs in an explicitly extended input-assembly contract before
Architect execution, with provenance/trust separate from unchanged user intent.
Do not overwrite raw_prompt/user_context to disguise derived observations as user
requirements, or smuggle REA through Architect notes. No report placeholders,
fetching, images, URLs, voice or multimodal changes are introduced now.

## Verification

`uv run pytest tests/test_architect_critic_pass.py` uses existing fake/gateway/AnyIO
conventions. Tests cover complete outcomes, exact preservation, blind messages,
source snapshots, fingerprints, tampered nested models, deterministic score binding,
failures/cancellation, retry/repair budgets and caller-owned resources. They block
database/live-provider/network use. The full backend suite retains API regressions
and SQLite/WAL/SHM snapshot guards. No real-model quality claims follow from passing.
