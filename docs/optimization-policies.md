# Completed evaluation and progress policies — M5D-3

## Scope and interfaces

This package adds pure in-memory data contracts and policy functions. It does not
execute agents, allocate IDs, connect to a database, call a provider, or implement
the optimizer. Existing create/revise, single-pass integration, APIs, rubric,
scoring, instructions and Critic input fingerprint algorithm are unchanged.

| Interface | Purpose |
| --- | --- |
| `EvaluationRound(round_number, evaluated, baseline_round_number=None, revision=None)` | One successfully completed, validated `EvaluatedCandidate`. |
| `EvaluationHistory(rounds=(... ,))` | Immutable ordered history containing one to three completed rounds. |
| `select_best_candidate(history) -> CandidateSelection` | Reference to an existing round and its input fingerprint, with selection policy version. |
| `check_revision_progress(history, attempted_revision_keys=(), protected_spans=()) -> ProgressDecision` | Select the best baseline; stop or advise one revision. |
| `check_candidate_progress(history, proposed) -> ProgressDecision` | Stop or advise evaluation of a validated, different candidate/revision outcome. |
| `revision_attempt_key(RevisionInput) -> str` | Stable SHA-256 digest of a validated revision request's actionable inputs. |

Contracts live in `backend/app/modules/prompts/optimization_contracts.py`, ranking
in `candidate_selection.py`, and progress functions in `optimization_progress.py`.
Selection policy **1.0.0** and progress policy **1.0.0** are separate explicit
values in returned objects. Revision policy remains **1.0.0**, rubric **2.0.0**,
and scoring **1.0.0**. A change to these rules requires policy-version review.

## Completed rounds, source identity and ancestry

Models follow the existing frozen, strict, extra-forbidden conventions. Python
history input requires a tuple; JSON arrays round-trip to immutable tuples.
Numbers must be consecutive and unique starting at 1, with no fourth round.
The first round has no baseline or revision provenance. Every later round needs
an earlier baseline number and a rewrite candidate. A baseline may be round 1
even when round 2 performed worse. It cannot be a self/future reference.

Nested instances are rebuilt from their fields, including on public helper entry,
so `model_copy(update=...)` cannot bypass validation. Existing `EvaluatedCandidate`
validation binds exact candidate/source/target/renderer to the Critic fingerprint,
checks literal evidence attribution, normalizes/validates the Critic result and
verifies its supplied deterministic score. This rechecks existing arithmetic; it
does not make a new quality assessment or dispatch a Critic. Selection returns
no new score or candidate.

Every round must have an equal original request snapshot, including UUID,
timestamp, exact raw prompt and context, preferences, output language, task profile
and target. `history.original_request` exposes the first immutable snapshot.
Workflow ID/owner references and renderer name/version also stay equal. Critic
agent/instruction/rubric and scoring versions remain pinned across history.
Non-null Architect AgentRun references cannot be reused across completed rounds.
Optional absent IDs remain absent; no records are invented. Matching IDs prove
local consistency only, never database existence, ownership or authorization.

When available, pass the existing `RevisedCandidate` as `round.revision`. Its
candidate must equal the evaluated candidate, its proposed round must match,
and its exact baseline must equal the referenced earlier round's candidate.
Its eligible feedback keys must equal the supported findings derived from that
baseline's own bound evaluation; existing validators check addressed-key claims,
execution context and protected source text. Progress checks apply these available
provenance checks to unchanged and clarification outcomes too.

This is the provenance available today, not authentication: M5D-2 outcomes do not
carry a digest of the full originating Critic feedback. Local issue keys alone
cannot prove historical feedback identity. Without an optional revision object,
the baseline number is a validated ancestry assertion, not proof that an agent
used it. The future executor must retain actual inputs/results and add any further
trusted provenance needed at its persistence boundary.

Clarification-only results, failures, and generated but unevaluated candidates are
excluded. Before initial evaluation a controller has no history, rather than an
empty/fabricated completed round. A failed dispatch is execution state; it does
not fill a gap in completed history. A qualified round followed by another manually
supplied valid round is permitted as historical data, but progress policy stops
as soon as the best existing round qualifies.

## Selection order and nulls

ADR-007 ordering is lexicographic:

1. Existing `scoring.passed=True` first, using the unchanged quality gates.
2. `scoring.gates.no_critical_issues=True` before a reported supported critical issue.
3. Higher known weighted total; a known value ranks above `None` in this tier.
4. Higher known intent-fidelity score; a known value ranks above `None` in this tier.
5. Earlier completed round for any remaining tie.

No substitute zero, partial total, renormalization, rounding, prompt-length reward
or new Critic call is introduced. `unassessable` retains its null total and cannot
qualify. If both totals are unavailable, comparison proceeds to known intent, then
the earlier round. An unassessable candidate with no *reported supported* critical
issue can outrank an assessable candidate with a critical issue because severity
precedes score. This is absence of a reported critical issue, not semantic safety.

For scores 82, 75, 80 with otherwise equal priorities, round 1 wins. A qualified
85 outranks a critical 92. A selected result is only the best available reference:
successful execution, quality qualification, selection, and missing assessment
evidence remain distinct. A nonqualified selection is never automatic approval.

## Duplicates and attempted revisions

Duplicate evaluation uses the **unchanged** Critic input fingerprint. With pinned
source/target/renderer it identifies exact rendered text, even when canonical
organization, Architect explanations or telemetry differ. History rejects repeated
fingerprints; candidate progress stops with `already_evaluated` before another
Critic operation. Text is not stripped, case-folded or whitespace-normalized.
Different indentation/newlines/quotes may be material. A different string is merely
eligible for evaluation, with no claim that it is semantically better.

Revision-attempt identity hashes canonical UTF-8 JSON containing:

- baseline Critic input fingerprint;
- supported feedback's criterion, code, severity, description, recommendation and
  attributed evidence, sorted independently of issue/evidence order;
- sorted declared protected source spans, since these change the actual request;
- revision policy and instruction versions.

Uncertain findings, local positional issue keys, scores, rationales/summary,
telemetry, current operation IDs and proposed round number are excluded. Baseline
identity still indirectly pins source ID/timestamp through its existing fingerprint.
Equivalent reordered feedback has the same key; changed substantive feedback,
baseline or protection changes it. This is exact deterministic identity, not
semantic equivalence or a security credential.

The caller owns a per-run registry of actually dispatched revision keys. Record
the key when dispatch begins, including an attempt that returns unchanged or
fails; merely asking for a progress decision must not consume it. Passing the same
key again returns `repeated_revision_attempt`. Helpers mutate neither registry nor
history. They do not implement retries, concurrent dispatch exclusion or recovery.

## Progress decisions and future M5D-4 use

`ProgressDecision` separates action (`revise`, `evaluate`, `stop`) from reason,
selected baseline, proposed next completed round, optional private attempt key and
progress-policy version. Only revision advice and a repeated-attempt stop carry
an attempt key. No result authorizes a fourth evaluation.

Both guards first select the best existing round and stop for `qualified`, then
`evaluation_limit`. A terminal candidate check does not consume its proposed input.
Revision advice then stops for `no_actionable_feedback` or
`repeated_revision_attempt`; otherwise it returns `revision_justified`. Eligibility
reuses existing supported-issue validation: severity, nonblank recommendation and
attributed evidence. Uncertain issues never become supported requirements; actual
semantic actionability remains a model/human judgment.

Candidate advice revalidates its input and binds revision outcomes to the selected
baseline and `len(history.rounds) + 1`. It stops for `unchanged_baseline`,
`clarification_required`, `already_evaluated` or `no_actionable_feedback`. Otherwise
it returns `new_candidate` / `evaluate`. New candidates must be rewrites under the
same pinned source/workflow/renderer and cannot reuse earlier Architect AgentRun
IDs. Bare `CandidateReady` is accepted for compatibility; it lacks revision evidence
and cannot itself prove which feedback was used. Prefer `RevisedCandidate` when
available. Invalid/tampered inputs raise fixed `LLMError(INVALID_REQUEST)`.

A future M5D-4 controller can:

1. Run the existing initial single-pass boundary. Handle clarification separately;
   append round 1 only after a valid completed evaluation.
2. Ask for revision advice using the per-run attempted-key registry. On continuation,
   build `make_revision_input()` from that selected round's own candidate/evaluation,
   with the advised next number and the same protected spans. Allocate/load actual
   operation records only in the later persistence layer.
3. Record actual dispatch, call Architect revision once, and ask for candidate advice.
   Route clarification separately; retain the prior baseline for unchanged output.
4. Independently evaluate an advised new candidate, construct `EvaluatedCandidate`,
   and append a new validated round with its available revision provenance. Re-select
   from the whole history. This sequence is documentation, not an implemented loop.

Failed/cancelled dispatches must not be retried indefinitely because history did
not grow. Future execution state must bound dispatches, deadlines and transport
work separately. Gateway retries do not create completed rounds. ADR-003's cap and
ADR-005's failure/interruption lifecycle remain in force.

## Privacy, persistence and verification

All text, evidence and feedback are untrusted data; policies never execute or obey
them. Ordinary representations hide content, metadata, fingerprints and attempt
keys. Public helper error paths discard private validation exception context and
log nothing. Explicit `model_dump*()`, structured validation errors and source
objects still contain private data and must not be logged or exposed unrestricted.
Hashes are for binding/duplicates only, not authentication or semantic proof.

No `WorkflowRun`, `AgentRun`, `PromptVersion` or evaluation record is created.
Future executor/repository transactions must load/authorize actual ownership,
enforce one active operation per run, reserve dispatch state atomically, and persist
only completed validated evaluations with round/counter updates together. Keep
LLM awaits outside database transactions. Final selection must atomically reference
an existing owned evaluated version; clarification must never become a version.
Evaluation persistence and interrupted-run recovery remain future work, including
the lack of a current clarification/waiting lifecycle status. No optimistic success
or extra database status is invented here.

`tests/test_optimization_policies.py` uses actual validated fake-provider single-pass
and revision outcomes, adversarial nested tampering, null/severity/tie scenarios,
exact text duplicates, provenance, idempotence, and private diagnostics. Pure helper
tests forbid provider/gateway/agent calls, SQL and network operations and check input
immutability. Full existing backend tests protect create/revise/API regressions.
Fake outcomes establish software contracts, not semantic quality or calibration.

Related: [single-pass boundary](architect-critic-pass.md),
[revision boundary](architect-revision.md), [ADR-003](adr/ADR-003-three-evaluation-rounds-cap.md),
[ADR-005](adr/ADR-005-in-process-task-runner.md), and
[ADR-007](adr/ADR-007-best-version-revision-baseline.md).
