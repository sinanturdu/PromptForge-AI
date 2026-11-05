# Durable readback and safe recovery assessment — M5E-5

## Persisted facts inspected before implementation

WorkflowRun owns the PromptRequest reference, versioned configuration/preferences,
source digest, execution token/lease, revision, current/completed round counters,
selected version/score/qualification, safe terminal details and timestamps. Its
phases are PENDING → GENERATING → EVALUATING ↔ REVISING, then COMPLETED, FAILED or
CLARIFICATION_REQUIRED. No RUNNING workflow state exists.

AgentRun reservations identify create/revise/evaluate operations, intended rounds,
logical keys, known requested settings and baseline/feedback/fingerprint binding.
RUNNING does not prove dispatch or completion. Completed Architect results may be
staged candidates, unchanged baselines or clarification, without PromptVersion.
Successful Critic completion, PromptVersion/Evaluation insertion, progress and
selected-best update commit together. Failed operations do not fabricate versions.

PromptVersion retains actual creator, global request version number, evaluation
round, exact canonical/rendered content and actual baseline parent. Evaluation
retains the independent Critic, input/rendering identity, all nine criteria, evidence,
policy versions, nullable scores and gates. Later failure/clarification retains
earlier completed evaluations and the best evaluated candidate.

M5E-4 execution_snapshot requires a historical token/exact current fence and uses
the write boundary. It remains an execution input, not general readback. Ordinary
API engine creation also applies WAL/NORMAL connection settings. Neither is suitable
as the default for independent recovery inspection. Readback therefore needs a
separate coherent read-only boundary without claiming, renewing or changing a journal.

## Internal entry points and privacy

Compose a separately owned `create_read_engine(database_url)` and
`WorkflowReadbackService(engine)`. No module wires them into application startup or
existing endpoints. Dispose the engine in the composition owner. No migration is run
and no default development URL is selected.

- `read(workflow_id, prompt_request_id=None)` returns immutable operational summaries,
  configuration/source digest, timestamps, actual operation/version/Evaluation IDs,
  stored counters/selection fields, recomputed eligible selection and typed findings.
- `read_content(...)` is an explicit sensitive projection adding exact source,
  validated completed EvaluationHistory, parsed operation results and terminal
  questions/details. Full dumps remain private and must not be logged.
- `list_workflows(prompt_request_id, limit=20, cursor=None)` returns 1–50 metadata
  entries using descending creation time then UUID and an explicit keyset cursor.
  Empty queries return an empty page. List entries explicitly do not claim full
  graph integrity validation; use a workflow read for that.
- `list_operations(workflow_id, prompt_request_id=None)` returns the bounded operation
  summary from one coherent validated workflow read.

No read requires an active claim or exposes execution tokens, client-idempotency
digests, logical operation keys, raw provider errors, request bodies, opaque provider
request IDs or gateway request IDs. Only validated whitelisted telemetry is projected;
unknown observations remain null. Known requested settings remain distinct from
observed metadata. Operational summaries exclude prompts, canonical content, Critic
rationales/findings, approved feedback and clarification text. Source digest and
configuration remain internal content-derived metadata, not public-safe credentials.
Recovery projections also omit those fields. Existing local ownership assertions
are not authentication. Public API/frontend consumption needs an authorization and
serialization review; these internal read models do not implement those features.

Each list page has its own coherent snapshot. Keyset ordering avoids offset drift;
it does not freeze an export across separate calls while new workflows are inserted.

## Snapshot boundary and bounded reconstruction

The lazy SQLite factory builds an existing-file URI with `mode=ro`, uses connection
`query_only=ON`, NullPool and hidden SQL parameters, and never creates a parent
directory or database. Caller URI options/authorities and in-memory URLs are rejected.
An explicit deferred `BEGIN` starts driver transaction control; the first SELECT
pins the snapshot. Five bulk queries load workflow, source, operations, versions and
Evaluations on that one connection. In-memory repository adapters feed the unchanged
M5E/M5D validators without N+1 queries. Projection happens inside the read unit;
rollback and permanent session/connection close complete before returning.

The reader does not call the write-engine factory or ordinary API engine factory,
issue BEGIN IMMEDIATE, set journal_mode/synchronous, take over a claim or modify any
row. It uses the existing journal. In WAL mode an independent writer can commit
while a reader retains its coherent earlier snapshot; a fresh read sees the newer
commit. Under DELETE journaling the same transaction gives consistency but can hold
up a writer until the short reader exits. SQLite can use its normal WAL/SHM locking
files for live reads; `mode=ro` does not mean an immutable database image. The unsafe
`immutable=1` assumption is deliberately not accepted for a concurrently written DB.

Logical order is round, Architect-before-Critic phase, operation start time and UUID;
versions use round/version number/UUID and Evaluations use round/UUID. These explicit
ties handle equal timestamps without inventing wall-clock execution order or
renumbering rows. At most six legitimate operations and three versions/Evaluations
exist under the current contracts. Queries fetch one overflow sentinel, flag
`history_size`/records_truncated and withhold validated history if that bound is
exceeded. There is no unlimited export.

Validation covers source digest/preferences/configuration, mirrored operation
bindings, creator/Critic ownership, exact staging/fingerprints, real baseline parents,
all criterion/evidence/scoring/gate/storage versions, telemetry mirrors, progress,
best selection and terminal evidence. It reuses `load_history`, `staged_candidate`,
`staged_revision`, `validate_best`, and existing selection/progress policies.
Incomplete operations and staged-only candidates remain distinct from evaluated
versions. Failed/clarification terminals retain earlier valid history. Null scores
are not zero. Contradictions produce deterministic typed IntegrityFinding codes and
record IDs, not repairs or dynamic exception text. Read failures that prevent row
decoding/query execution return fixed typed errors rather than fabricated projections.

Stored selection fields are labeled as stored facts. `selected` is derived from
validated completed history; a mismatch produces a progress_selection finding and
blocks exact reconciliation. A valid historical Evaluation can still be shown when
separate terminal/operation metadata is inconsistent. Findings always take precedence
over a recovery classification claiming a consistent workflow.

The schema has no independent selection-policy-version column on Evaluation. The
projection's `computed_selection_policy_version` names the current existing selector;
it is not invented historical metadata. Existing configuration pins optimizer/progress
versions and Evaluation pins rubric/scoring/storage/rendering evidence. Future policy
evolution must preserve versioned reconstruction or explicitly revise storage; this
readback cannot prove which arbitrary historical binary wrote unsupported rows.

## Recovery assessment, without recovery execution

`WorkflowRecoveryAssessmentService(readback, clock=utc_now).assess(...)` reads one
snapshot and compares persisted leases with an aware injected UTC clock. Equality
means expired, as in the write services. It does not renew or reset anything.

| Confirmed snapshot condition | Diagnostic disposition |
| --- | --- |
| PENDING and unclaimed, without contradictory records | No action required; no execution authorization is implied. |
| Active with a valid lease, even without visible progress | Currently active; do not interfere. |
| Expired active lease with RUNNING operations | Inspect operation outcome; provider replay remains unsafe. |
| Expired active lease, no unresolved operation, durable results available | Reconcile committed persistence identity. |
| Expired claim without result evidence | Manual review required; do not infer worker liveness. |
| Consistent COMPLETED terminal | Confirmed completed, no action required. |
| Consistent FAILED or CLARIFICATION_REQUIRED terminal | Retain prior history, manual review required. |
| Broken graph, progress, telemetry or terminal evidence | Integrity investigation required. |

Conditions can coexist: an active/expired workflow may have both earlier committed
Evaluations and an unresolved current operation. Proofs identify actual committed
operation/Evaluation/terminal records. RUNNING proves a durable reservation, not that
a provider was or was not invoked. Missing Evaluation never proves no provider call.
Expired lease never proves the former worker stopped. External outcomes, worker
liveness and caller acknowledgment remain explicitly unknown. Committed-result
reconciliation availability is a database fact, not a claim that an ACK was lost.
Every assessment says automatic provider replay is unsafe and grants no mutation
authority. It creates no new status, key, token, revision or persisted record.

Clock skew can change active/expired classification. `observed_at` is assessment
time, not a database-server timestamp; the snapshot may be superseded by a concurrent
commit. Neither this local clock nor any disposition coordinates multi-host leases.
Those limitations reinforce read-only recommendations rather than authorizing action.

## Exact read-only reconciliation

`reconcile_evaluation(EvaluationCompletion)`,
`reconcile_terminal(WorkflowFinalization)` and
`reconcile_operation(OperationExpectation)` use the same coherent read boundary.
They never invoke completion/finalization writers or providers. Expectations are
sensitive input contracts, not safe log payloads.

Evaluation matching checks ownership, historical token, reserved Critic UUID/logical
key/round, creator, exact bound candidate, complete assessment/scoring/policy evidence
and known metadata against validated durable history. A confirmed match returns the
stored PromptVersion/Evaluation IDs, current completed count/selected candidate and
whether progress exceeds the caller's prior count. Original operation identities
stay stable even when later rounds change selection. An uncompleted reservation
returns reserved_unresolved, not a fabricated receipt or proof that no model call
happened. Conflicting/absent identities are explicit; corrupt graphs return
integrity_inconsistent and cannot establish exact commitment.

Generic operation matching additionally checks full declared identity, binding,
requested parameters and expected stored result/metadata. Supplying no expected
result can establish only committed_identity_only, never an identical-result claim.
Terminal matching requires the exact versioned terminal payload and any supplied
operation evidence, plus consistent durable history/status. A logical key alone is
never sufficient. Receipts never echo tokens or content and always deny mutation
authorization. Exact matching with an expired historical token is a diagnostic read,
not a valid claim for a new write.

There is no persisted acknowledgment flag or per-operation historical CAS revision.
Reconciliation therefore proves the validated artifact under established ownership/
idempotency fields, not delivery to the original caller, the exact old expected
revision, an external provider's side effects or permission to resume. Future/new
fences are refused; prior counts must match the operation's intended round. Old
caller revisions may legitimately predate current progress, as in M5E-3C replay.

## Verification and remaining scope

Tests use actual persistence services and the opt-in coordinator to create realistic
committed fixtures through FakeLLMProvider, then inspect without provider access.
They cover one-to-three rounds, quality/nulls, lineage, staged/unfinished operations,
early/failed terminals, contradictory permitted data, SQL-enforced constraints,
read-only writes/missing-file rejection, fixed query counts, immutable projections,
concurrent commits, physical connection cleanup/cancellation, clock semantics,
pagination, exact/conflicting reconciliation and secret-free diagnostics. Corruption
tests retain foreign-key/unique enforcement; no constraint bypass is used.

Use the approved private Python 3.12.16 runtime with SQLite 3.53.1 on both actual
drivers, aiosqlite 0.22.1 and SQLAlchemy 2.1.6. Run the new readback/recovery tests,
full backend pytest, strict mypy (`app tests migrations benchmarks`), Ruff lint/format,
Git whitespace and unchanged development DB size/mtime/hash/sidecar checks.

PostgreSQL read-snapshot composition is **not implemented or qualified**: the factory
rejects it instead of mixing statement-level snapshots under an unverified default
isolation. A future PostgreSQL implementation must establish/test one read-only
repeatable snapshot, cancellation cleanup and its concurrency interactions. No
dependency, schema, migration, existing API engine or default runtime changes here.

M5E now has internal write, explicit execution, durable readback and assessment
boundaries. Automatic resume/replay, token takeover, RUNNING resets, startup scans,
recovery workers, schedules, public endpoints and frontend screens remain absent.
Any future recovery mutation needs a separately reviewed evidence/fencing protocol;
these read models can support a future authorized UI without implementing it.
