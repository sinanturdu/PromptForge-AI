# Atomic evaluation persistence and workflow finalization — M5E-3C

## Internal boundary, without workflow execution

`WorkflowPersistenceService.complete_evaluation(EvaluationCompletion)` and
`finalize_workflow(WorkflowFinalization)` extend the M5E-3B service. They do not call
providers, dispatch agents, activate the optimizer, expose endpoints or start a
runner. Commands reuse the existing validated `EvaluatedCandidate`, `WorkflowFence`
and versioned terminal/storage contracts. Receipts are immutable, detached values
returned after commit and session close. No ORM object escapes the write unit.

EvaluationCompletion identifies the completed Architect, reserved independent
Critic, logical Critic operation key and evaluation round. Its bounded evaluated
candidate includes exact content, assessment, recomputed scoring/gates, policy
versions and observed call metadata; it does not accept caller-selected best IDs or
an unbounded history. EvaluationReceipt returns the version/Evaluation identity,
round, current workflow progress, current selected best IDs/quality and replay flag.
FinalizationReceipt returns current workflow/selection and versioned terminal details.
These values contain private content and must not be logged through full serialization.

## One short completion transaction

Each call opens a fresh M5E-3A `write_transaction()` on the dedicated engine. SQLite
reserves the writer with BEGIN IMMEDIATE before reading allocation/progress state.
Repositories flush but never commit independently. A new completion:

1. Reloads source ownership, preference/content binding, configuration and claim.
2. Reconstructs all completed history (at most three rounds) and validates progress
   and stored selection against that history.
3. Revalidates the completed Architect result, canonical/rendered content, rendering
   versions, source fidelity, and actual creator operation/context.
4. Verifies the reserved Critic belongs to this workflow/round and is bound to this
   exact creator and blind-input fingerprint. Architect notes are not Critic input.
5. Validates all nine criterion assessments, evidence attribution and the existing
   scoring/gate result. Assessment, rubric, instruction, fingerprint, scoring and
   storage versions remain explicit. Nullable criteria and totals remain null.
6. Allocates MAX(version_number) + 1 across every version of the PromptRequest,
   including historical workflows and manual versions. The unique constraint is the
   final guard. Inserts exact PromptVersion and Evaluation projections.
7. Completes the Critic AgentRun with the Evaluation reference and whitelisted,
   actually observed metadata. Unknown fields remain null; requested provider/model
   declarations are checked separately from observed execution.
8. Reconstructs the augmented validated history, derives the deterministic best and
   stop decision, and conditionally updates progress/selection/state revision.
9. Commits once. Any failure rolls back version, Evaluation, Critic and workflow
   writes together. Earlier committed rounds survive.

The write unit never spans a provider await. Fake-provider test artifacts are
created before entering it. A failed Critic never creates a version or Evaluation.

## Durable lineage and existing policies

The source PromptRequest remains the owner. Version rows retain workflow, actual
Architect creator, round, exact canonical representation and rendered bytes, target
mode, renderer identity, changes and creation time. A revised version's parent is
looked up from the validated baseline Evaluation, never from the latest inserted
version. Cross-record request/workflow/operation/baseline relationships are checked
in addition to database foreign keys. Individually valid UUIDs are insufficient.

`workflow_history.py` reconstructs existing M5D EvaluationHistory, revision inputs and
outcomes from durable bounded rows. It checks complete storage projections rather
than trusting persisted summary scores or caller-supplied history. Unsupported
versions, inconsistent evidence/gates, orphan workflow versions, mismatched source,
fingerprints, parents, progress or selected IDs fail closed; they are not repaired.

Selection calls the unchanged `select_best_candidate`: qualified first, then no
supported critical issues, higher known total, higher known intent fidelity, and
earlier round for ties. Unknown never becomes zero. A best-available candidate is
distinct from approval. Completed count equals persisted validated Evaluations,
not attempted calls, gateway retries, staged candidates or clarification results.

Stopping delegates to existing M5D progress checks. Qualified output stops first;
the configured 1–3 evaluation limit then takes precedence over other early reasons,
matching the optimizer. Otherwise revision requires supported actionable feedback
and a new revision-attempt key. Completion leaves EVALUATING if stopping is due,
or moves to REVISING/current_round=completed_rounds+1 if another round is justified.
Normal terminalization is an explicit separate service action.

An earlier better/tied baseline remains selected. With unchanged feedback/protected
source configuration, revising that same baseline again repeats M5D's attempt key
(which intentionally excludes round/telemetry). It must stop as
`repeated_revision_attempt`, not manufacture a third round. Valid three-round tests
therefore use improving baselines. The storage lineage always follows the actual
validated baseline; no duplicate-attempt exception was added to produce a fixture.

## Idempotency, fencing and version allocation

The reserved Critic UUID and logical operation key identify completion. An identical
retry validates the stored candidate, assessment/scoring, metadata, round, source,
operation relationships and all durable history before returning existing IDs.
It creates no version, Evaluation or progress increment. Conflicting data fails.
The replay receipt reports the workflow's current progress/best, which may have
advanced since that particular completion; its own version/Evaluation IDs are stable.

An already committed exact completion or terminal result can be read for
reconciliation with the same stored execution token after its lease expires and
with the original revision/count. A different token is rejected. Every NEW write
requires the current token, live lease, permitted phase and exact revision/count;
the conditional update repeats those predicates. Historical token/lease values are
retained, not renewed. This read-versus-write distinction applies to the two M5E-3C
actions; existing M5E-3B reservation/staging retries retain their live-lease rules.

There is one persistence attempt per invocation, no implicit retry or provider
replay. A version uniqueness collision rolls back and returns `conflict`; the caller
may explicitly retry the SAME computed completion in a fresh transaction. SQLite's
writer reservation serializes normal cooperating allocations. The existing active
workflow index prevents two simultaneous active runs for the same PromptRequest.
Tests cover independent active requests, a terminal historical replay racing a new
same-request completion, and a forced allocation collision with an existing manual
version. The index is not relaxed to manufacture concurrent active same-request runs.

Commit acknowledgments can be lost after durable commit. Errors/cancellation still
propagate; no misleading success is returned. Re-submit the same completion or
finalization to inspect committed identity, without rerunning Architect or Critic.
Before-commit failures/cancellation roll back. Connections close in both paths.
Expected errors expose only fixed PersistenceError codes without private exception
chains; unexpected cancellation is not swallowed.

## Terminal outcomes and retained history

Finalization reconstructs history and selection again. It accepts only existing
TerminalDetailsStorage reasons, validated against the actual workflow/operation:

| Outcome | Durable behavior |
| --- | --- |
| Qualified, evaluation limit, no supported feedback or repeated attempt | COMPLETED; retain selected best with actual qualified/unqualified/unassessable quality. |
| Unchanged or already-evaluated revision | COMPLETED from validated staged revision evidence and existing progress rules; no new version/Evaluation. |
| Initial/revision clarification | CLARIFICATION_REQUIRED with exact validated questions and operation evidence; retain earlier history/best, if any. |
| Failure before/after evaluations | FAILED with safe code/operation classification; retain all earlier Evaluations and best selection, or null selection for zero rounds. |

Completed execution requires evaluated history and never fabricates quality scores.
Zero-round failure/clarification is legitimate. A current reserved operation can be
failed atomically with finalization; absent observed failure metadata stays null.
Callers with observed failure telemetry can use existing `fail_operation` first.
Unrelated running operations or a pending later round cannot masquerade as normal
completed execution. Explicit evidence must belong to the actual current round.

Repeated terminal requests require the exact details/status and consistent scalar
stop/error/finished fields; conflicting requests fail. Reconciliation never resets
progress, rewrites retained history or changes a terminal outcome. M5E-3B's existing
failure/clarification transitions remain available and can be reconciled here.

## Verification and remaining boundaries

`tests/test_evaluation_persistence.py` uses temporary migrated file databases, WAL,
FULL/FK and independent connections. Tests exercise three rounds, null scores,
selection/lineage, cross-record tampering, exact replay, concurrent/conflicting
completion, number collisions, early/failed/clarification terminals and visibility
from another reader before commit. Faults after version/Evaluation flush, before
Critic completion, after progress SQL, during selection/finalization and before
commit must leave the operation unchanged. Lost acknowledgments after actual commit
and cancellation/physical connection cleanup are tested separately. Existing M5D
and M5E-3B tests are unchanged. Fake assessments establish contract behavior, not
real-model semantic quality or power-loss durability.

Verification uses the approved private Python 3.12.16 runtime; both actual sqlite3
and aiosqlite connections report SQLite 3.53.1 (aiosqlite 0.22.1, SQLAlchemy 2.1.6).
Use the private-interpreter commands in [the service guide](workflow-persistence-services.md),
plus `-m pytest tests/test_evaluation_persistence.py -q`. No dependencies, default
environment, ORM schema, migrations, existing API engine or development DB change.

PostgreSQL execution/concurrency is unverified. BEGIN IMMEDIATE supplies SQLite's
single-writer guarantee; MAX+1 alone is not a PostgreSQL allocator. Before PostgreSQL
or multi-host use, qualify source-row/allocator locking, isolation, consistent lock
order across B/C operations, deadlock/transient-conflict handling and lease-clock
skew. SQL uniqueness/fencing remains a final guard and does not justify automatic
model replay. Source immutability against external writers is still a deployment
requirement, not enforced by these digest checks.

M5E-4 still owns opt-in optimizer/service integration, actual dispatch and runtime
deadline/stop enforcement. M5E-5 owns interrupted-run inspection/recovery and any
explicit resumption policy. No runner, renewal/takeover, background replay, public
workflow API or automatic recovery is introduced here.
