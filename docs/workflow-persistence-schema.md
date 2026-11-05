# Workflow persistence schema and storage contracts — M5E-2

Current implementation: M5E-3B adds internal operation persistence and M5E-3C adds
[atomic evaluation completion/finalization](atomic-evaluation-persistence.md) using
this unchanged schema. The M5E-2 scope/history below is not a claim of workflow
execution availability; optimizer activation and recovery remain deferred.

## Implemented scope

Revision `0002_workflow_persistence`, after `0001_initial_schema`, adds schema and
pure storage projections. There are five application tables: `prompt_requests`,
`workflow_runs`, `agent_runs`, `prompt_versions`, and `evaluations`. There are no
criterion/issue child tables. Existing request endpoints and the M5D optimizer are
unchanged. No workflow repositories, execution services, observer hooks, background
tasks, recovery, new API or automatic migrations are activated.

Source files: `backend/app/models/`, `backend/migrations/versions/0002_workflow_persistence.py`,
and `backend/app/modules/prompts/persistence_contracts.py`.

## One Evaluation table

This supersedes the older planned `evaluations` + `criterion_scores` +
`evaluation_issues` layout. The MVP needs bounded complete assessments and
run/version queries; versioned structured JSON avoids unnecessary joins and child
row synchronization. Per-criterion analytics can justify normalization later.

Evaluation stores UUID identity, owning workflow/version/Critic operation,
evaluation round, exact input fingerprint, evaluated target/renderer identity,
rubric/scoring versions, assessment schema version, structured assessment,
quality status, nullable Numeric(5,2) total, passed flag, versioned gates,
completion timestamp and record timestamp. UUID/UTC conventions are reused.

The assessment is `AssessmentStorage(schema_version="1.0.0", assessment=CriticOutput)`:
all nine complete criterion entries, null scores, rationales, literal evidence,
supported/uncertain issues, severity/recommendation and summary. Gates use
`GateStorage` with the same explicit storage version. There is no legacy `grade`
field. A total remains null for unassessable output; no zero/partial total is invented.

Uniqueness is enforced for `(workflow_run_id, evaluation_round)`,
`(workflow_run_id, prompt_version_id)`, `(workflow_run_id, input_fingerprint)` and
`critic_agent_run_id`. Round values must be 1, 2 or 3. SQL checks cover digest length,
target, total range and null/status/passed consistency; they do not calculate
weights, qualification thresholds, evidence attribution or semantic quality.

Composite RESTRICT foreign keys bind the version and Critic operation to the same
workflow as the Evaluation. Parent tables have supporting named unique constraints
on `(id, workflow_run_id)`. Workflow ownership is also a RESTRICT foreign key.
There is no cascade deletion of history.

## WorkflowRun additions and terminal states

New fields are `request_snapshot`, `request_content_digest`, `state_revision`,
`execution_token`, `lease_expires_at`, `client_idempotency_key_digest` and
`terminal_details`. All are nullable for legacy rows except `state_revision`,
which starts at server/Python default 0 and has a nonnegative check. The claim token
and lease must both be absent or both present. Digest lengths are checked.

The versioned preference snapshot contains the source UUID/timestamp, target,
language/profile and three preferences. **It does not copy raw_prompt or user_context.**
Those remain in the existing PromptRequest. No update API is added; M5E-3 must
verify the digest before use and enforce source immutability at the service boundary.
No database trigger prevents direct out-of-band PromptRequest edits.

The v1 request digest hashes canonical UTF-8 JSON with sorted keys and explicit
existing source fields, including exact raw text/context, identity, timestamp and
preferences. Prompt whitespace, quotes and code are not normalized. This algorithm
is separate from, and does not change, M5D's Critic fingerprint or revision key.
Digests are integrity references, not credentials; do not log them casually.

Existing config/progress/final-selection/error/timestamp fields are reused. A unique
request/idempotency-digest constraint permits multiple null legacy keys.

Workflow states remain PENDING, GENERATING, EVALUATING, REVISING, COMPLETED and FAILED,
with new terminal CLARIFICATION_REQUIRED. There is no WorkflowRun RUNNING state.
The partial unique index `uq_workflow_runs_active_prompt_request` covers the four
nonterminal states and permits later explicitly requested workflows after terminal
outcomes. SQLite and PostgreSQL predicates are declared. This is a database guard,
not a pre-insert application query.

`state_revision` is storage for future conditional updates; a CHECK does not enforce
monotonic increments. Claims, expiry, admission and counter transitions are not yet
operational. Interrupted work will be FAILED with a safe interruption classification.
Clarification stores questions without creating an evaluation or prompt version.

## AgentRun identity, results and unknown facts

New fields: `operation_key`, `revision_attempt_key`, `baseline_evaluation_id`,
`result_kind`, `error_code`, `binding_data`, `result_data`,
`feedback_projection_digest` and `call_metadata`. They are nullable for legacy data.
Logical operation keys and non-null revision-attempt keys are unique within a run.
Revision keys are internal identity, not public API idempotency or authorization.

The baseline Evaluation reference uses RESTRICT. This required feedback-provenance
link introduces a dependency back to Evaluation; the migration adds it after both
tables exist. ORM relationships distinguish the evaluated Critic operation from
an Architect operation's baseline evaluation explicitly.

Existing provider/model columns retain requested/configured identifiers when known;
they are now nullable. Observed provider, requested/resolved models and usage are
distinct fields in whitelisted CallMetadataStorage. Provider/model, attempt_count
and repair_attempted may all remain null when unobserved. Existing values are
preserved. Their ORM defaults no longer invent one attempt or no repair for an
incomplete operation. Input/output token and latency fields remain nullable.

Operation results classify `candidate`, `unchanged`, `clarification`, `evaluation`
or `failed`. Typed result envelopes permit only the matching payload. Candidate
content is bounded and source-referenced; evaluation results reference an existing
evaluation. Binding envelopes hold revision baseline references, protected spans,
eligible issue keys and approved feedback/attempt digests. No full request/history
or provider response is serialized into these envelopes.

## Pure Pydantic storage projections

All storage versions are explicitly required and currently **1.0.0**. Unknown,
missing, null or inconsistent versions are rejected. Models are frozen, strict,
extra-forbidden and hide private fields in ordinary representations. Nested
model_copy tampering is revalidated in Python and JSON modes.

| Contract | Purpose |
| --- | --- |
| RequestPreferenceSnapshot / request_content_digest | Preference/source reference and separate exact-content digest. |
| AssessmentStorage | Complete resolved CriticOutput, retaining nulls and exact evidence. |
| GateStorage / ScoringStorage | Existing GateResults / ScoringResult in explicit versioned envelopes. |
| EvaluationStorage.from_evaluated() | Deeply validate the existing bound EvaluatedCandidate, then project explicit row fields. |
| CandidateStorage.from_candidate() | Exact canonical/rendered agreement and original-preserve validation through the existing candidate contract. |
| OperationBindingStorage / OperationResultStorage | Bounded revision binding and outcome-specific payloads; no records created. |
| CallMetadataStorage | Null for unknown metadata; explicit required observation counters when metadata is present. |
| TerminalDetailsStorage.from_outcome() | Only safe terminal classification/questions; no outcome history or copied candidate. |

Use `.from_json_value(database_json)` to read database JSON in strict JSON mode.
It returns fixed safe INVALID_REQUEST errors for malformed/private payloads. Direct
Pydantic validation and structured error details still require restricted handling.
Full serialization contains private content and must not become application logs.
No projection invokes an agent, provider, network connection or database session.

EvaluationStorage reuses ScoringEngine for consistency, rather than implementing
another scoring system. Its `record_values()` is an explicit allowlist; Critic call
metadata is not copied into the Evaluation JSON. Standalone storage data lacks the
original source needed to prove quote attribution; future writes must derive from
the actual validated EvaluatedCandidate and check loaded records transactionally.

## Deferred M5E-3 invariants

M5E-3B's [internal persistence services](workflow-persistence-services.md) now enforce
source binding, creation/operation idempotency, fenced claims, operation budgets and
revision provenance. The list below records schema-only limitations; final selection
and atomic PromptVersion/Evaluation/progress writes remain M5E-3C responsibilities.

Schema alone cannot guarantee:

- PromptVersion's request matches its workflow's original request.
- Final selection belongs to that workflow and has a completed valid Evaluation.
- Parent/baseline is an earlier evaluated candidate selected by ADR-007.
- An AgentRun is the correct role/operation and its baseline belongs to its workflow.
- All required new-format metadata is present for newly admitted workflows.
- Source preferences/digest, target/renderer, JSON versions, scalar score/gates and
  evidence agree with the actual stored source and bound candidate.
- Progress reflects completed evaluations, or state revisions/claims advance safely.

M5E-3 must load/validate these relationships in short service-owned transactions,
use conditional writes and enforce the unchanged optimizer budget/selection rules.
No session may remain open across LLM awaits. UUID equality and hashes are not
authorization. PromptVersion fields, sources, request/version and creator uniqueness
remain intact; parent need not mean immediately preceding version. No is_selected
flag is added: WorkflowRun.final_version_id remains the selection reference.

## Migration safety and reversibility

Upgrade preflight refuses duplicate legacy active workflows without changing their
status or deleting rows. All old column values, including exact source content and
circular final-version references, are copied unchanged. Legacy snapshots are not
fabricated. Newly nullable metadata retains all existing recorded values.

Populated SQLite tests proved that ordinary batch rebuilding with foreign_keys=ON
fails on referenced workflow/agent tables. Alembic's dedicated SQLite connection
therefore temporarily disables that connection's FK enforcement **outside** the
transaction, explicitly issues BEGIN IMMEDIATE, runs all migration DDL and the
revision stamp, checks foreign_key_check before commit, and restores its original
FK setting on success or failure. An injected mid-migration failure rolls back both
schema changes and stamp. Runtime connection/session settings are unchanged.

Injected SQLite migration connections must be idle; caller-owned transactions are
rejected rather than silently committed. Run online migrations on a dedicated
connection. Take a verified backup before any separately approved data migration.
The development database was not migrated by this milestone.

Downgrade to 0001 is allowed only if no Evaluation or new-format metadata/claims,
clarification state, nonzero state revision or legacy-incompatible null telemetry
would be lost. It refuses before mutations otherwise. No history is deleted to
force a downgrade. Safe populated legacy round trips and refused downgrade logical
database dumps are tested. Restoring a compatible backup is a separate operation.

## Runtime/durability gate and verification

Verification used Python 3.12.7, SQLite **3.45.3**, SQLAlchemy 2.1.6 and Alembic 1.20.1.
No global Python/runtime or driver package was changed. SQLite's documented rare
WAL-reset bug affects this version range. **Before M5E-3 enables concurrent durable
writes, select and verify a patched runtime** (3.51.3 or a documented fixed backport).
See [SQLite WAL documentation](https://www.sqlite.org/wal.html).

Application connections still use WAL, synchronous=NORMAL and legacy driver
transaction control. The migration-specific explicit BEGIN does not fix runtime
multi-statement service transactions. M5E-3 must deliberately choose/test explicit
BEGIN control (SQLite writer reservations may use BEGIN IMMEDIATE) and use short
conditional-update transactions, not SELECT FOR UPDATE row locks. For durability
across power loss, recommend verifying WAL + synchronous=FULL; NORMAL has a different
commit-durability guarantee. No development database PRAGMAs were changed.
References: [SQLAlchemy SQLite transactions](https://docs.sqlalchemy.org/en/21/dialects/sqlite.html#transactions-with-sqlite-and-the-sqlite3-driver),
[Alembic SQLite batch/FK handling](https://alembic.sqlalchemy.org/en/latest/batch.html).

M5E-3A now supplies a separate opt-in [runtime gate and explicit write unit](sqlite-write-transactions.md)
with BEGIN IMMEDIATE and FULL/FK enforcement. Existing API/migration engines retain
their behavior. The current affected runtime is refused for the new WAL path; safe
DELETE-mode transaction tests are implemented. No workflow persistence or runtime
upgrade is activated by this foundation.

Run from backend: `uv run pytest tests/test_workflow_persistence_schema.py tests/test_persistence_contracts.py tests/test_migrations.py`, then full backend lint,
format, strict typing and pytest. Tests use migrated temporary file databases,
preserve the development DB guard and make no real LLM calls. PostgreSQL DDL is
compiled, not executed against a PostgreSQL server. Synthetic tests do not prove
power-loss durability, real-model quality or production readiness.
