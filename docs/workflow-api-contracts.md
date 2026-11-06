# Workflow API contracts and gated read inspection — M5F-1 / M5F-2

## Implemented boundary and inspected architecture

M5F-1 implements Pydantic contracts, pure allowlisted read projections and two validated
settings. M5F-2 adds the six GET routes below, registered only with
`WORKFLOW_API_ENABLED=true`. Both flags default off; enabling both still activates
reads only. No POST workflow route, writer, task, provider dispatch, automatic recovery
or database migration/provisioning is introduced. Creation/start remain M5F-3 design.

The current `main.py` lifespan owns one lazy ordinary DatabaseSessionManager and
one provider/gateway through AsyncExitStack. The ordinary SQLite connection hook
uses WAL/NORMAL on connection; it is not the dedicated persistent writer and must
not serve this new write path. Provider construction is not a generation request.
Before M5F-2 only `/health` and PromptRequest create/get/list routes were available.
Their behavior remains unchanged. The frontend/MSW and Playwright tests use those existing prompt-request
contracts; none currently invokes the optimizer.

M5E provides the necessary internal behavior:

- `WorkflowPersistenceService.create_workflow()` binds an existing PromptRequest,
  immutable preferences/configuration and source digest. Its optional client key is
  hashed, scoped to the request and checked against the exact configuration/source.
  Conflicting active workflows are rejected by the existing unique index.
- `claim_workflow()` atomically compares PENDING, revision, ownership, empty token,
  lease and round counters. `execute_persisted_optimizer()` makes this initial claim;
  a future runner must not pre-claim and then call this entry point a second time.
- The adapter reserves each AgentRun before provider dispatch, closes the transaction,
  stages Architect output, and commits evaluated versions atomically. Its leases,
  state revisions, operation identity and bounded optimizer remain authoritative.
- `WorkflowReadbackService` uses an independently owned read-only snapshot engine.
  It offers metadata/content reads, 1–50-entry keyset pages and integrity validation.
  `WorkflowRecoveryAssessmentService` is diagnostic only: no replay or mutation
  authorization, including after an expired lease or lost acknowledgment.
- RFC 9457 handlers already sanitize validation failures and unexpected exceptions.
  New API code must use them rather than returning raw persistence/provider errors.

ADR-004's `/runs` naming and approximately two-second polling are retained. ADR-005's
planned in-process runner is still absent. Its original startup interruption mutation
must not be implemented without a separate recovery/ownership decision: M5E evidence
does not prove that the former worker stopped or a provider call never happened.

## Endpoint inventory (GETs implemented; POSTs remain planned)

All paths below are relative to `/api/v1`. UUID path parameters identify existing
records; request ownership must be checked in the service scope, never inferred from
possession of a UUID. No user/principal ownership column exists in this local MVP.

| Method / path | Request | Response / success | Gates |
| --- | --- | --- | --- |
| POST `/prompt-requests/{prompt_request_id}/runs` (planned only) | `WorkflowCreateRequest` | `WorkflowCreateResponse`: 201 new, 200 exact replay; Location `/api/v1/runs/{id}` | API + execution |
| POST `/runs/{workflow_run_id}/execution` (planned only) | `WorkflowStartRequest` | `WorkflowStartResponse`: 202 only after bounded runner admission; Location points to progress | API + execution |
| GET `/runs/{workflow_run_id}` | UUID | `WorkflowProgressResponse`, 200 | API |
| GET `/prompt-requests/{prompt_request_id}/runs` | `WorkflowHistoryQuery` | `WorkflowHistoryPageResponse`, 200 (including empty pages) | API |
| GET `/runs/{workflow_run_id}/history` | UUID | `WorkflowMetadataHistoryResponse`, 200 | API |
| GET `/runs/{workflow_run_id}/history/content` | UUID, explicit content retrieval | `WorkflowContentHistoryResponse`, 200 | API; sensitive |
| GET `/runs/{workflow_run_id}/result` | UUID | `WorkflowResultResponse`, 200 for an integrity-valid terminal workflow; otherwise 409 | API; sensitive |
| GET `/runs/{workflow_run_id}/recovery` | UUID | `WorkflowRecoveryResponse`, 200; internal development diagnostics | API; internal only |

Creation never starts execution. Both mutating operations require execution activation,
so read-only API activation does not initialize a dedicated writer. Reads create no
workflow. A missing request/run is 404; invalid UUIDs, payloads and cursor pairs are 422.
Response content is never added to metadata routes through an `include_content` default.
The internal list service returns an empty page for a missing parent as well as an
empty history. The HTTP route explicitly checks the parent through the existing
PromptRequestRepository and a separate read-only snapshot before calling that service.
It does not borrow the ordinary write-capable API session or alter list-service behavior.
All `/runs/{id}` routes accept an optional `prompt_request_id` UUID assertion. The
existing service enforces it as a parent-scope filter, returning the same 404 as an
absent workflow. This assertion is not a principal, authentication or authorization.

The API requires `client_idempotency_key` for creation, although the internal service
allows it to be absent. Accept 1–256 ASCII letters/digits/`.`/`_`/`:`/`-`, starting
with a letter/digit; no whitespace, trimming or case normalization. This is an opaque
identity, not a secret or authorization credential. Pass it unchanged to the existing
service, never echo/log it or its stored digest. The same key/configuration/source
returns the existing run, including a terminal run; a changed configuration/source
or a different key competing with an active run returns 409 `workflow_conflict`.
Do not silently substitute an unrelated active run. The create body exposes only
`max_evaluation_rounds` (1–3, default 3). Protected spans, versions, model settings,
provider credentials, timeout policy and lease duration remain server-owned.

Start requires an explicit nonnegative integer `expected_revision`. The workflow ID
is the natural execution identity; no new start-idempotency column is invented.
Duplicate/concurrent starts must not invoke the provider again. A confirmed admission
may return the same 202 receipt; after an ambiguous admission, return conflict or
unavailable and direct the caller to poll/readback. Never treat an expired lease,
unresolved reservation or missing Evaluation as permission to start again. The
existing entry point rejects already claimed/terminal workflows before provider work.

History uses `limit` (default 20, max 50) and paired `before_created_at` (timezone-aware)
and `before_workflow_run_id`. `to_cursor()` returns the existing WorkflowCursor.
Responses use the existing `{created_at, workflow_run_id}` next cursor and descending
time/UUID order. There is no fabricated total count or frozen cross-page export.
List entries have `integrity_checked=false`; fetch progress/history for graph findings.

## Serialization, quality and privacy

`workflow_api_schemas.py` reuses WorkflowRunStatus, AgentRunStatus, hard round limits,
TerminalDetailsStorage's supported reason vocabulary, TokenUsage, VersionSummary,
EvaluationSummary, SelectedEvaluation, CanonicalPrompt, CriticOutput, WorkflowCursor
and RecoveryAssessment. No parallel scoring, selection or lifecycle policy is added.
Request models forbid extras and reject coerced round/revision booleans and strings.
The read contracts appear in FastAPI/OpenAPI only with API activation; creation/start
schemas remain unpublished because their routes are not registered.

Progress includes actual `current_round`, `completed_rounds`, `max_evaluation_rounds`
and `state_revision`. `stored_best_version_id`, `stored_final_score` and
`stored_qualified` are labeled stored facts, not trusted approval. `selected` comes
from validated history and is withheld if there are findings, truncation or invalid
history. A terminal result likewise withholds its candidate/assessment on inconsistency.
History still shows actual stored Evaluation/version metadata for investigation;
validated historical prompt content can remain visible beside those findings.
The result route maps integrity findings/invalid or truncated history to 409
`workflow_integrity_inconsistent`. The pure projection itself withholds trusted
content and returns findings; it does not raise HTTP exceptions or change readback.

`COMPLETED` means technical completion, independently of `selected.quality_status`
(`qualified`, `not_qualified`, `unassessable`). `FAILED` or CLARIFICATION_REQUIRED can
retain an earlier evaluated candidate without becoming successful. Initial clarification
has questions but no candidate, version, Evaluation or score. Clarification is terminal
for polling; answering it requires a later explicit request, not implicit resumption.
Missing scores remain JSON null. Decimal scores retain Pydantic's decimal-string JSON
representation when known. GateResults remain nullable/typed, never inferred from length
or JSON validity. No scorer/optimizer/gate changes are made here.

Metadata excludes prompt text, canonical content, Architect notes, Critic explanations,
evidence quotes, clarification questions, content digests and configuration. Public
telemetry exposes only observed usage/counts/duration; unknown telemetry or token
counts stay null. Provider/model/request identifiers are omitted altogether. The
content endpoint explicitly exposes source, evaluated canonical/rendered prompts,
change summaries and Critic assessments, keeping notes outside copy-ready text.
Staged unevaluated operation bodies are left to internal readback, not treated as versions.

Never serialize internal WorkflowState, WorkflowFence, operation/storage results,
PersistentOptimizationResult or gateway exceptions directly. No response includes
execution tokens, operation keys, idempotency digests, credentials or private exception
chains. Ordinary reprs hide content; dumps and structured validation errors remain
sensitive and must not be logged. Content/progress/history/recovery responses
use `Cache-Control: no-store` in M5F-2; do not put prompts, keys or provider payloads
in URLs, access logs or tracing context. Recovery proofs are evidence, never authority.

Errors retain `application/problem+json`, existing ProblemDetails fields and
server-generated X-Request-ID. The read router maps expected errors without changing
global handlers or existing PromptRequest error codes. Write-only codes remain reserved:

| HTTP | Condition / error_code |
| --- | --- |
| 404 | API routes absent: existing `not_found`; resource absent: `prompt_request_not_found` / `workflow_not_found` |
| 403 | Failed local request guard: `workflow_access_denied`; future disabled writes: `workflow_execution_disabled` |
| 409 | Source/configuration/idempotency/claim conflict: `workflow_conflict`; active result: `workflow_not_terminal`; trusted result blocked: `workflow_integrity_inconsistent` |
| 422 | Malformed input: existing `validation_error` |
| 429 | Bounded admission full: `workflow_capacity_exceeded` with safe Retry-After |
| 503 | Explicit failed runtime preflight: `workflow_runtime_unavailable`; unavailable persistence/admission: `workflow_unavailable` |
| 500 | Unexpected failure: existing `internal_server_error` |

`workflow_api_disabled` is reserved for a future explicit internal gate error; the
ordinary disabled router is absent/404. Service `PersistenceError` not_found/conflict/
invalid/unavailable map to fixed public messages at the read route boundary.
Stored provenance invalidity is an integrity/infrastructure failure, not automatically
a client 422. Runtime errors wrapped as unavailable must not be misclassified using
private exception chains. A provider failure after accepted execution is reported
through FAILED progress/result, not retroactively as a failed admission response.

## Activation and future execution safety

### Implemented M5F-2 request boundary and ownership

The workflow-only ASGI middleware runs before route validation and database dependencies.
It uses Starlette's own routed-path normalization so an ASGI root_path cannot bypass
the boundary. Existing PromptRequest and health routes keep their original policy.

- Require a literal IP in the ASGI socket peer and verify loopback with `ipaddress`.
  IPv4 127/8, IPv6 `::1` and IPv4-mapped loopback peers qualify; names, absent peers,
  scope identifiers and remote/mapped-remote addresses do not. Headers never substitute
  for socket identity. Production has no trust override; TestClient injects an explicit
  transport peer in tests.
- Require exactly one Host: `127.0.0.1`, `localhost` or `[::1]`, optionally with a
  numeric port 1–65535. Reject aliases, userinfo, malformed/duplicate hosts and arbitrary
  domain names. There is no DNS lookup or trust in DNS resolution.
- Reject every Forwarded, X-Forwarded-* and X-Real-IP header, including empty values.
  Run the direct local server with proxy rewriting disabled (`--no-proxy-headers`).
  Do not place a proxy that strips those headers in front of this API: a loopback
  proxy's socket does not prove its original client was local.
- When present, require a single Origin matching the actual scheme, Host name and
  effective port. Reject opaque/null, different origins and non-origin URL components.
  Reject Sec-Fetch-Site values other than `same-origin`/`none`, including `same-site`.
  Absent browser headers allow direct local CLI clients only after peer/Host checks;
  their absence is never proof that a remote caller is trusted.

All workflow responses (success, 403/404/405/409/422/503 and sanitized unexpected 500,
including disabled-path 404s) have Cache-Control=no-store, Vary=Origin,Sec-Fetch-Site,
Cross-Origin-Resource-Policy=same-origin, X-Content-Type-Options=nosniff and
X-Frame-Options=DENY. No CORS allowance is added. The middleware handles unexpected
pre-response errors through the existing sanitized error handler inside its header
wrapper; no exception chain, SQL parameters or raw Pydantic input is sent to clients.

The enabled lifespan owns one lazy mode=ro engine plus the existing readback/recovery
services through AsyncExitStack. It opens no connection at startup. Every inspection
uses the existing short `read_snapshot()` transaction with a fresh NullPool connection;
no ordinary API engine/session, BEGIN IMMEDIATE, writer or journal-setting operation
is used. Shutdown and partial startup failures dispose the engine; read service state
is removed on shutdown. API=false never constructs the reader. Non-file SQLite or
unsupported database configurations fail explicitly rather than falling back to a writer.
Missing files return sanitized 503 without creating directories or databases.

Expected PersistenceError mappings are not_found→404 workflow_not_found,
conflict→409 workflow_conflict, invalid→409 workflow_integrity_inconsistent and
unavailable→503 workflow_unavailable. Direct parent-lookup SQL errors also become
sanitized 503. Malformed HTTP inputs remain 422. Unexpected failures remain 500.
Result integrity takes precedence over non-ready classification; a valid active run
returns 409 workflow_not_terminal. Terminal failure/clarification without a candidate
returns a truthful 200 with null candidate/assessment and any approved questions.
Recovery retains stored diagnostics but withholds selected results on findings;
committed proof/reconciliation availability and replay/mutation safety still come from
the unchanged M5E assessment service.

The cursor is a typed value boundary, not an authorization token or a promise that
its referenced row still exists. Parent filtering always applies independently of the
cursor. Lists are bounded to 50, have deterministic time/UUID ties and no total count.
The parent existence check and bounded listing are separate read snapshots; the API
does not promise an atomic export across those checks or across pages.

Reverse proxies, containers, public/LAN deployments, tunnels and multi-user use remain
unsupported without a separately reviewed authenticated security design. Local processes
share this unauthenticated trust boundary. No claim is made that Host/Origin checks or
CORS authenticate a user, or that unchanged legacy PromptRequest routes gain these
protections. Keep the whole MVP bound to loopback. This package has no execution or
recovery mutation endpoint.

### Reserved write/execution design (not implemented)

`WORKFLOW_API_ENABLED=false`, `WORKFLOW_EXECUTION_ENABLED=false`. Accept boolean
values or case-insensitive exact `true`/`false` environment strings; reject ambiguous
`1`/`yes`, null and whitespace-bearing values. Execution=true requires API=true.
These flags are intent, not authentication, binding enforcement or runtime qualification.
Settings validation performs no database work. M5F-2 adds only a lazy read engine and
readback/recovery services to the lifespan when API=true; execution remains inactive.

ADR-008 restricts the unauthenticated MVP to an explicitly bound `127.0.0.1` server
(including any development proxy). No provider-backed route may be published on
`0.0.0.0`, LAN, a tunnel or an unrestricted reverse proxy. The actual server/proxy
binding must be enforced by deployment/runner composition, not merely APP_HOST or an
environment declaration that Uvicorn does not consume automatically. Before future
activation, reject unexpected Host headers and browser origins (including DNS rebinding
and cross-origin mutation), require JSON for writes, and restrict the dev proxy.
These are additional browser defenses, not authentication/authorization; local processes
can still read/write this unauthenticated MVP. Public/multi-user exposure requires real
authentication, per-request ownership authorization and reviewed quotas first. CORS
never grants authorization. Recovery diagnostics should remain disabled outside the
internal development surface; no takeover/replay/resume endpoint is proposed.

The future composition owner must explicitly qualify connected SQLite before any
dedicated write, then use the existing gated WAL/FULL/FK writer. Unsafe or unverified
versions fail before journal changes; no DELETE fallback. Use the approved private
Python 3.12.16 runtime locally and the existing CI runtime qualification in both backend
and E2E jobs; never assume the earlier SQLite 3.45.3 is safe or infer Linux SQLite from
Windows. Enabling flags must not migrate/provision the development database. Provisioning
and migration remain explicit operator decisions outside HTTP/startup. Read-only
activation uses the existing existing-file read engine, not a WAL writer.

The future runner, not the HTTP response task, owns execution and strong references
to its tasks. Start admission must be bounded and deduplicated before returning 202;
initially allow one active workflow with a bounded pending capacity, fixed server-owned
deadline/model/token policy, max three evaluations (at most six agent operations) and
existing gateway attempt/repair caps (at most four attempts per operation, 24 per run).
Reject excess admission before claiming. The worker calls
`execute_persisted_optimizer()` once; its conditional database claim decides the winner.
Short transactions finish before each provider await. Do not add provider retries at
the API/runner layer. This prevents duplicate dispatch by concurrent requests but
cannot promise exactly-once billing after a crash or unknown external outcome.

FastAPI BackgroundTasks is not a durable queue: installed Starlette Response sends
the body and then awaits the task; BackgroundTasks runs its tasks sequentially inside
that process. There is no durable admission record, restart recovery or bounded pool
in that mechanism, and a post-response failure cannot change the sent 202. An
in-process supervised runner is acceptable only with these explicit limits: loss on
restart is observable, queued PENDING runs are not automatically replayed, and M5E
readback/recovery must assess interrupted claims without guessing a provider outcome.
Durable queue/dispatch-acknowledgment semantics remain a later design, not an implied
guarantee of this contract or the existing database schema.

On shutdown stop admission, drain within a bounded grace period, then cancel/await
remaining tasks before closing the caller-owned provider and read/write engines.
Request disconnect/cancellation must not cancel an admitted worker. Worker cancellation
still propagates through M5E cleanup; unknown in-flight/commit outcomes remain unknown.
Do not fabricate FAILED, renew an expired claim, rotate a token or reissue a paid call.
Lease duration must exceed the whole bounded gateway operation plus persistence margin;
M5E renews only between operations and rejects late writes with stale fences.

Clients poll progress about every two seconds, with one request in flight, backoff on
transient errors/429 and a stop on COMPLETED/FAILED/CLARIFICATION_REQUIRED. Polling never
starts work or authorizes recovery. Fetch sensitive history/result only explicitly.

## Follow-up packages and acceptance

- **M5F-2:** implemented the six guarded GET routes, read-only engine ownership and
  safe error mapping. Offline HTTP tests cover default-off routing/OpenAPI, loopback/
  origin/Host/forwarding attacks, root_path, parent scope, pagination, states, privacy,
  null scores/telemetry, integrity conflicts, no writes/provider calls and cleanup.
- **M5F-3:** separately review creation/start admission and supervised runner ownership,
  bounded capacity/deadline/token limits, actual bind/proxy/Host/origin enforcement,
  runtime qualification, concurrent duplicate starts, crash/admission ambiguity and
  cancellation/shutdown. Only then activate a writer or provider-backed execution.
  A durable start-admission acknowledgment would require additional design/schema work;
  none is claimed or added by M5F-1.

Verification uses the existing approved private runtime, fake providers and temporary
migrated databases only. Focused contracts cover defaults/invalid activation, route
absence/no database connection, UUIDs/idempotency/cursors, all real statuses, null
scores/telemetry, separate content, retained failed candidates, clarification, integrity
findings and FastAPI/OpenAPI compatibility. Full backend pytest, strict mypy including
migrations, Ruff lint/format and development database hash/mtime/size/sidecar guards
remain required. Frontend sources, dependencies, CI, models and migrations are unchanged.
