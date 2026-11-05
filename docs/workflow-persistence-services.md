# Workflow and Agent persistence services — M5E-3B

## Implemented, without execution activation

`WorkflowPersistenceService` uses the M5E-3A dedicated write engine and M5E-2
models/storage contracts. It opens a fresh `write_transaction()` for each action;
repositories execute/flush and never commit or roll back. Engine disposal belongs
to the composition owner. No session is retained in the service or returned to a
caller. Receipts are immutable Pydantic values and arrive only after commit/close.
The existing request-scoped PromptRequestService and APIs are unchanged.

There are no provider calls, optimizer hooks, workflow endpoints, background runner,
normal Evaluation writes, final selection or recovery. Service methods are internal
Python interfaces, not available HTTP APIs. Do not call the legacy PromptRequestService
inside the new unit: it owns its separate API commit.

| Service method | Boundary / return |
| --- | --- |
| `create_workflow(request_id, configuration, client_idempotency_key=None)` | Load/validate an existing source; insert PENDING or return an exact explicit-key retry. Returns WorkflowCreation. |
| `claim_workflow(request_id, workflow_id, expected_revision=..., lease_seconds=...)` | Conditional PENDING → GENERATING claim, fresh UUID token, bounded lease. Returns WorkflowState. |
| `reserve_operation(fence, identity, binding, revision_input=None)` | Reserve one logical create/revise/evaluate operation as RUNNING. Returns OperationReceipt. |
| `complete_architect(fence, agent_run_id, outcome)` | Validate an existing Architect handoff/revision and stage its result; no version/Evaluation creation. Returns OperationReceipt. |
| `fail_operation(fence, agent_run_id, error_code=..., metadata=None)` | Atomically record AgentRun failure and FAILED workflow classification. Returns OperationReceipt. |

`WorkflowState.fence()` captures request/workflow IDs, token, expected revision and
completed-round count for the next mutation. `replayed=True` explicitly means no new
dispatch was authorized, including when the existing AgentRun is still RUNNING.
The caller must not repeat an LLM operation in response to such a receipt.

Repositories expose scoped operations rather than generic CRUD:

- WorkflowRunRepository: `get_by_id`, `get_for_request`, `get_idempotent`,
  `get_source`, `insert`, conditional `claim`/`advance`, `refresh`, and related-row
  `get_evaluation`, `get_version`, `evaluations` reads.
- AgentRunRepository: `get_by_id`, `get_for_workflow`, `get_operation`,
  `get_revision`, `for_round`, `insert`, conditional `finish` and `refresh`.

## Creation, source binding and configuration

Creation loads the actual PromptRequest and validates it through existing request/
Architect contracts. WorkflowConfiguration is a version-1 persistence configuration
for existing M5D options: maximum evaluations 1–3, optional positive finite timeout,
explicit protected spans and current optimizer/progress versions. It does not start
or enforce a running optimizer deadline. Protected offsets are checked against the
actual source; duplicate/out-of-range spans are refused.

The run stores M5E-2 RequestPreferenceSnapshot plus request_content_digest, not copied
raw_prompt/user_context. The digest algorithm is unchanged: SHA-256 of the explicit
source allowlist serialized as sorted compact UTF-8 JSON, including UUID/timestamp,
exact text/context and preferences. There is no whitespace or Unicode normalization.
Every claim/reservation/completion reloads and rechecks the snapshot and digest.

New services never update PromptRequest. There is currently no source-edit API. Any
future writer must enforce immutability once workflows reference a request, or create
a new source record instead; FKs do not prevent direct content edits. Out-of-band edits
are detected at the next action, not prevented by a new trigger. Legacy workflows
without valid versioned binding data are refused; no metadata or backfill is invented.

Client keys are nonblank strings of at most 256 characters. Only SHA-256 of their exact
UTF-8 bytes is stored. An identical key is scoped to its PromptRequest and must match
the full validated configuration, source reference/preferences and source digest.
It may return a historical terminal run. A changed payload conflicts. No key, or a
different key, never silently adopts an active run. The existing partial unique index
is the final concurrency guard; terminal runs allow a new explicitly requested run.
Keys/digests/UUIDs are not authentication, authorization or proof of semantic quality.

## Claims, fencing and bounded operations

The claim uses a conditional UPDATE RETURNING and accepts exactly one row: matching
PENDING state, expected revision, no token/lease, and zero current/completed rounds.
It sets GENERATING/current_round=1, a fresh execution token, start time and a lease of
1–900 seconds, then increments state_revision. SQLite BEGIN IMMEDIATE reserves the
writer before these reads/updates. No SELECT FOR UPDATE or implicit takeover is used.

New operation mutations require loaded request ownership, the current token, a live
lease, expected phase/revision/completed count, and current_round=completed+1 within
the configured limit. Completed Evaluation round numbers must agree with the stored
counter. Conditional updates check ownership/token/lease/status/revision/completed
count again and advance state_revision. Claims and mutations commit before any future
provider await. One Architect operation and one Critic operation may be reserved per
round; gateway retries do not allocate additional AgentRuns.

Creation phase is GENERATING, revision phase REVISING, and Critic phase EVALUATING.
The service does not advance completed-round counters or invent later rounds.
M5E-3C must atomically advance those when its Evaluation writes succeed.

An identical reservation/result retry is a read-only replay and may carry the original
expected revision after the first action advanced it. It still checks the current
token/live lease/source and exact stored identity/data. A conflicting retry fails;
completed operations cannot be replaced. No lease renewal, lease transfer or stale
replay is implemented. Terminal token/expiry values are retained as historical fencing
identity for bounded identical-result retries; terminal status still prevents new
mutations. Expired owners are refused even for these retries.

## Operation storage, provenance and outcomes

OperationIdentity explicitly supplies logical key, role, agent/instruction versions
and optional configured provider/ModelSettings. These are declarations, not observed
execution. AgentRun provider/model columns hold known requested configuration.
The version-1 OperationParameters envelope in the existing `parameters` column holds
those settings and, for Critic reservation, the candidate AgentRun UUID and blind input
fingerprint. It contains no Architect explanations or provider response body.

Revision bindings reuse M5D's `revision_attempt_key()` unchanged. The distinct approved
feedback digest hashes version-1 sorted compact UTF-8 JSON containing the ordered
supported RevisionFinding keys and complete issue content. It retains exact evidence.
The service recomputes both from a validated RevisionInput and compares actual rows:
source → workflow → evaluated version → creator/Critic operations → Evaluation.
It checks role/status/round/ownership, canonical/rendered content, renderer/target,
fingerprint, assessment schema, stored metadata, and existing ScoringEngine/gate
consistency. Revision-parent references are checked against their declared evaluated
baseline. Protected spans and eligible issue keys must match the approved binding.
It validates the supplied earlier completed baseline; selecting the best baseline
and deciding whether to revise remain M5D/orchestrator responsibilities.

Architect completion requires the actual reserved AgentRun ID and matching declared
agent/instruction versions in the existing handoff/revision context. Preserve stays
byte-for-byte equal to the source. Results use OperationResultStorage; canonical/
rendered prompt and user notes stay separate from CallMetadataStorage. Metadata is
an explicit allowlist: unknown execution fields stay null, while an explicitly
observed zero-attempt failure can record zero. Unknown observed provider/model values
do not contradict known requested settings. No credentials or exception bodies are
accepted as metadata. Full serialization still contains private text and must not be
logged. Callers must supply None when observations are unavailable.

| Completed operation outcome | Workflow handling in M5E-3B |
| --- | --- |
| Accepted initial/revised candidate | Stage AgentRun candidate JSON, transition to EVALUATING. No PromptVersion/Evaluation. |
| Unchanged revision | Stage unchanged result, remain REVISING. A later policy integration decides the normal stop. |
| Clarification | AgentRun COMPLETED, workflow CLARIFICATION_REQUIRED, questions in terminal details. No new version/Evaluation. |
| Failure, including Critic failure | AgentRun FAILED, workflow FAILED with safe classification. Existing progress/history/selection fields remain untouched. |

There is deliberately no successful Critic completion method: its AgentRun completion,
PromptVersion/Evaluation insertion, progress and selection must commit together in
M5E-3C. Technical completion never fabricates quality qualification. Failure does not
calculate a new best selection or reset prior history; later integration reconciles it.

Expected errors have fixed `not_found`, `conflict`, `invalid` or `unavailable` codes.
Unique identity conflicts are distinguished from other database failures. Translation
occurs outside transaction-context exception handling, removing private database/
validation exception chains. Cancellation propagates unchanged after cleanup. No
automatic workflow/provider retry occurs. ADR-008 local ownership checks are not auth.

## Verification, limitations and next packages

Tests run with the approved private Python 3.12.16 environment, actual sqlite3 and
aiosqlite connections both reporting SQLite 3.53.1; aiosqlite 0.22.1, SQLAlchemy 2.1.6.
They migrate only temporary file databases and assert WAL/FULL/FK settings. New WAL
tests fail explicitly on an affected runtime rather than silently skipping or using
DELETE. The default Python/environment, lockfile and development database are unchanged.

The matrix covers exact source binding, invalid/copy-mutated commands, idempotency,
independent concurrent creation/claims/reservations, stale tokens, phases/revisions,
lease bounds/expiry, cross-workflow provenance, mirrored column tampering, preserved/
revised/unchanged/clarification/failure outcomes, nullable telemetry, error privacy,
rollback after flushed writes, and cancellation/connection closure. Existing API and
optimizer regressions remain in the full suite. Completed Evaluation history is seeded
only by explicit test fixtures simulating future M5E-3C, not by production service code.
Fake-provider assertions establish contracts, not real-model semantic quality.

```powershell
$taskPython = 'C:\Users\sinan\AppData\Local\PromptForge\sqlite-qualification\backend-venv\Scripts\python.exe'
& $taskPython -m pytest tests/test_workflow_persistence.py -q
& $taskPython -m pytest -q
& $taskPython -m mypy app tests migrations benchmarks
& $taskPython -m ruff check .
& $taskPython -m ruff format --check .
```

PostgreSQL service/concurrency execution is not tested here. Conditional updates are
portable, but lock isolation, database time versus application-clock lease skew, and
source immutability must be qualified before multi-host deployment. External edits or
database corruption cannot be repaired by digests. Cancellation at a successful commit
can leave acknowledgment uncertain; an identical retry must reconcile stored identity,
not replay a model call. These tests do not prove power-loss durability.

M5E-3C owns atomic candidate/Evaluation writes, completed-round progress and final
best-candidate references. M5E-4 owns actual opt-in optimizer/service integration and
deadline/stop enforcement. M5E-5 owns stale-run recovery. ADR-003/005/007 policies are
unchanged. There are no new endpoints, migrations, tables or public workflow APIs.
