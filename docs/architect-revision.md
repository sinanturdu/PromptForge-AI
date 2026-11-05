# Feedback-driven Architect revision — M5D-2

## One revision, no optimizer

```python
from app.agents.revision_schemas import ProtectedSourceSpan, RevisedCandidate
from app.modules.prompts.revision_integration import make_revision_input

# evaluated is a caller-selected, existing EvaluatedCandidate.
revision_input = make_revision_input(
    evaluated,
    next_evaluation_round=2,
    protected_spans=(
        ProtectedSourceSpan(source="raw_prompt", kind="version", start=start, end=end),
    ),
)
result = await architect.revise(revision_input, request_id=correlation_id)
if isinstance(result, RevisedCandidate):
    candidate = result.candidate  # eligible for later independent Critic evaluation
```

This operation uses the existing injected gateway/model settings exactly once via
generate_json(). It adds no agent retry, Critic call, score, record, selection engine
or workflow loop. Transport retries/JSON repair remain gateway-owned. Errors and
external cancellation propagate; the caller retains provider ownership. Existing
create(), creation instructions and public handoff contracts are unchanged.

## Input and feedback provenance

RevisionInput holds the frozen baseline CandidateReady, its own CriticResult,
proposed next evaluation round, protected spans and current revision context. Its
original request is the baseline's immutable snapshot, not an unrelated payload.
Use make_revision_input(EvaluatedCandidate, ...): the factory revalidates the completed
outcome, including its existing deterministic score. This verifies a prior judgment;
the revision operation itself does no scoring and returns no new quality score.

Input/agent validation deeply checks nested models, including model_copy() tampering,
candidate/source/target/renderer fingerprint binding and all evidence attribution.
Feedback is derived only from that evaluation's supported issues with severity,
nonblank description/recommendation and grounded evidence. Uncertain issues are
filtered. No eligible finding means INVALID_REQUEST before provider access.

Feedback is a read-only projection, not a caller-editable field. Keys use original
one-based issue positions: issue_001, issue_002, etc. Filtering an uncertain first
issue leaves the second as issue_002. CriticIssue is reused rather than duplicating
severity/evidence rules. Exact duplicates are rejected by existing Critic validation;
differently worded semantic duplicates still require judgment. Quotation matching
proves attribution, not interpretation, actionability or actual model accuracy.

Default revision context retains request/workflow ancestry but clears the baseline
creation AgentRun ID. Optional ArchitectRunContext must match that ancestry and
must not reuse the prior AgentRun ID. No records or UUIDs are created. Local UUID
links/hashes do not prove database existence, authorization or authenticity.
Proposed evaluation round is 2 or 3; it does not increment completed evaluations,
verify history length or implement ADR-003/007 selection/round policy.

## Model input and versioned policy

Revision instruction version **1.0.0**, revision operation version **1.0.0**,
Architect version **1.0.0**. Creation instruction remains **1.0.0** unchanged.
The model receives only an explicit allowlist: original content/preferences without
ID/timestamp, exact baseline text, supported issue key/criterion/code/severity/
description/recommendation/evidence, and declared protected source text/kind.

Scores, qualification, full history, Critic rationales/summary, provider telemetry,
baseline fingerprint, proposed round, workflow IDs and Architect notes are excluded.
Never serialize the full local RevisionInput as the model message.

All source text and feedback are untrusted data, not policy. Critic suggestions
cannot authorize changes that contradict original intent. Preserve hard/negative
constraints, code, versions, quotes and creative freedom; invent no technologies,
budgets, deadlines, features, examples or output formats. Avoid personas, artificial
reasoning steps and padding. Useful specificity is not length growth or score gain.
Preserve is allowed when no meaningful safe revision is justified. Clarification
is reserved for blocking missing/contradictory information, not optional details.

## Wire output and application outcomes

RevisionOutput extends ArchitectOutput with required addressed_issue_keys. Existing
canonical, decision and note validators are reused. All wire keys remain required;
unused scalars are null and collections empty. Extra bounds/key checks are local,
so the schema passes the existing strict structured-output preflight.

Rewrite must claim at least one eligible issue key; preserve/clarify claim none.
Unknown keys fail locally without another semantic-repair call. Keys claim fixes,
not verified resolution. User-facing changes remain CandidateReady.changes, outside
rendered text. No independent approval is asserted.

| Status | Result | Behavior |
| --- | --- | --- |
| revised_candidate | RevisedCandidate | Changed CandidateReady(decision="rewrite"), with source/context, canonical/rendered agreement, protected material and claimed keys validated. No score. |
| unchanged_baseline | UnchangedBaseline | Exact existing baseline; no new candidate, questions, fixes or evaluation. reason is preserve or identical_rewrite. |
| clarification_required | RevisionClarification | Focused questions, existing baseline/request binding and new operation metadata; no new candidate/evaluation/version. |

All outcomes separate the prior baseline from current revision context/telemetry,
fingerprint, proposed round and policy versions. Initial create preserve still
means original raw_prompt. Revision preserve means the selected baseline, which
may already differ. It never constructs CandidateReady(decision="preserve") for
rewritten baseline text. An identical rendered rewrite becomes unchanged_baseline;
its new changes/claimed fixes are discarded while actual telemetry is retained.

## Narrow protected source spans

Spans use Python character offsets [start, end) into raw_prompt or user_context,
with kind version/quotation/code/identifier/literal/formatting. Callers explicitly
declare genuinely exact material; no speculative protection rules are inferred.
Empty/out-of-bounds/null-source spans, duplicates and more than 32 spans fail.
Full raw-prompt protection, including combined coverage leaving only whitespace,
is rejected to permit legitimate rewriting. A whole supplied context code block
can be protected. Limits come from existing source bounds, not the Critic's
600-character evidence limit.

Every protected text must occur exactly in a revised rendered prompt, including
indentation/newlines. Loss fails INVALID_STRUCTURED_OUTPUT before a candidate is
returned, without silent fixes or extra calls. Missing baseline material may be
restored by rewrite. Preserve/identical rewrite returns only the unchanged baseline,
not a new protected or improved candidate, and does not resolve prior findings.
Literal presence does not prove full fidelity, placement, multiplicity or absence
of contradictions; protect a combined span for exact surrounding format/order.

## Compatibility, limits and next work

Revision needs existing CandidateReady/CriticInput as well as ArchitectRequestSnapshot.
These data-only contracts and unchanged projection/fingerprint helpers now live in
architect_contracts.py, without agent implementation imports. architect_integration.py
re-exports the same objects at every existing public path. No duplicate snapshot
or deferred circular import is introduced. Shared evidence/binding validation lives
in critic_validation.py; the original public Critic validator import remains valid.

Ordinary representations hide content, feedback and metadata. Explicit dumps remain
private. Validation failures use fixed safe errors without private exception context.
The agent logs no content. Fingerprints and supplied relationships are not security
credentials; later persistence must verify real ownership/state transactionally.

M5D-3 can add selection/progress rules; M5D-4 can evaluate revised candidates under
the three-round cap. Neither is implemented here. No ADR-003/007 workflow policy,
API, persistence, migration, frontend, dependency or multimodal/REA feature changes.

Run `uv run pytest tests/test_architect_revision.py` and full backend checks. Tests
cover both baselines, all decisions, exact/long spans, provenance, uncertainty,
claims, nested tampering, JSON round trips, privacy, cancellation and import paths.
Fake scripts verify software behavior, not real improvement, semantic safety,
prompt-injection resilience or calibration. Human benchmark reviews remain pending.
