# Execution admission and security foundation — M5F-3A

M5F-3B now supplies the separately composed [supervisor and explicit startup](execution-supervisor.md).
The M5F-3A inventory below records the preparation boundary; ordinary startup remains
unqualified, and M5F-3C HTTP POST admission is still absent. Only the dedicated M5F-3B
startup connects actual listener proof, provider/writer ownership and supervised tasks.

## Implemented and deliberately unwired

`app/api/execution_admission.py` contains typed admission input/decisions and one
thread-safe, process-owned capacity gate. It imports no repository, engine, optimizer
or provider and creates no async task. No application state, lifespan, route registration,
GET response or configuration default changes. Both flags remain false by default.
API=true/execution=false continues to expose only the reviewed M5F-2 reads; both=true
still exposes those same reads. There is no POST handler, queue, workflow runner,
workflow creation or automatic recovery in this package.

`AdmissionRequest` binds the existing WorkflowStartRequest.expected_revision to actual
PromptRequest/WorkflowRun UUIDs. Input is snapshotted/revalidated once, including extras
and strict revision validation. The gate consumes freshly loaded internal
WorkflowReadback, never a client-supplied history or a token from an API response.
The existing graph integrity findings, immutable configuration and actual statuses
remain authoritative. No new persisted status is added.

## Decisions, precedence and errors

Both feature flags are required before any positive decision. Next check the request
transport and input, then shutdown/owned-server/runtime qualification, persistence
availability, owning scope and existing state. Integrity findings/truncation/invalid
history block eligibility before trusting other facts. Unresolved or incomplete
operations block dispatch even when a lease expired. The local reservation check
and one-slot allocation run under the same short threading lock with no await.

| Admission outcome | Future HTTP / stable error_code |
| --- | --- |
| feature_disabled | 403 workflow_execution_disabled |
| security_denied | 403 workflow_access_denied |
| environment_unverified | 503 workflow_execution_environment_unsafe |
| invalid_request | 422 validation_error |
| workflow_not_found (including parent mismatch) | 404 workflow_not_found |
| workflow_not_eligible | 409 workflow_conflict |
| already_running | 409 workflow_conflict |
| already_completed | 409 workflow_conflict |
| unresolved_operation | 409 workflow_conflict |
| duplicate_admission | 409 workflow_conflict |
| capacity_exhausted | 429 workflow_capacity_exceeded; Retry-After: 2 |
| runtime_not_qualified | 503 workflow_runtime_unavailable |
| persistence_unavailable / shutting_down | 503 workflow_unavailable |
| admission_permitted | internal reservation only; no HTTP acceptance yet |

AdmissionDecision.to_error() supplies fixed sanitized AppError values for the existing
RFC 9457 handler. It never attaches input, SQL/provider exceptions, source digests,
credentials, identifiers or fencing tokens. These mappings are not wired into current
GETs. Positive decisions explicitly retain durable_claim=false and
authorizes_provider_call=false. Return only approved decision/problem projections to
future clients, never the internal AdmissionResult/reservation/runtime/server objects.

## Eligibility and duplicate semantics

Only an integrity-valid, untouched PENDING workflow can reserve a slot: matching
parent/run IDs and revision, state_revision/current_round/completed_rounds all zero,
no claim/lease, operations, versions, evaluations or selected candidate. Stored round
configuration must match, and its existing timeout_seconds must be present and at most
300 seconds. Legacy unbounded configurations are rejected, not edited. Existing source
and configuration validation still runs again in the actual M5E claim.

- First eligible request reserves one local slot, not a database execution claim.
- An identical concurrent/repeated request finds that reservation and conflicts;
  changing its revision cannot obtain another slot. One concurrent contender wins.
- Another eligible workflow while the slot is occupied gets 429; nothing is enqueued.
- COMPLETED returns already_completed regardless of qualification. FAILED and
  CLARIFICATION_REQUIRED require a separate explicit new workflow, not resumption.
- GENERATING/EVALUATING/REVISING with an unexpired lease are already_running. An
  expired/missing lease is ineligible; no automatic reclaim or token rotation occurs.
- An unresolved operation takes precedence over lease-based admission. Missing
  Evaluation or lost acknowledgment never proves a paid request did not happen.
- close_admission() is irreversible for the gate. Shutdown rejects even duplicates;
  the owner can release existing capacity but cannot reopen it.

The opaque reservation handle releases only its own current slot by object identity;
a stale/different handle cannot free a replacement. Release is not completion or
authorization to retry. The bounded gate retains no unbounded tombstone/history table.
After release/restart a stale PENDING snapshot might again pass local checks; this is
why the future worker must reload state and call execute_persisted_optimizer() once.
That entry point makes the conditional claim itself. Never pre-claim and then invoke
it again, dispatch from an admission receipt, or repeat provider work after ambiguous
claim/reservation outcomes. Local reservations are not durable admission receipts.

Creation remains the existing independent service operation: a client key digest is
scoped to a PromptRequest and compared against immutable source/configuration. The
same key replays that exact workflow; mismatches or another active workflow conflict.
It does not start work. No new start-idempotency column is invented. Existing M5E
creation/claim/concurrent-dispatch tests remain the authority for the durable behavior.

## Actual listener and request security

Existing startup commands in root/backend README use ordinary Uvicorn with --reload;
backend/.env.example's APP_HOST/APP_PORT are not consumed as a verified socket binding
by Settings/create_app. Uvicorn also supports proxy rewriting and worker environment
defaults. Those commands remain valid for the existing MVP/read API but **do not
qualify execution**, even with both feature flags true.

`prepare_local_execution_server()` is an explicitly called context manager, not a CLI
or an automatic startup hook. It accepts only literal 127.0.0.1 or ::1, binds/listens on
an exclusively owned socket (Windows SO_EXCLUSIVEADDRUSE; no SO_REUSEPORT), and prepares
one Uvicorn Server with workers=1, reload=false, proxy_headers=false,
forwarded_allow_ips='', no root_path/UDS/FD/TLS fallback. IPv6 is v6-only. Occupied ports
fail; there is no reuse of an unrelated server. Port zero exists for isolated tests.

Preparation alone is unqualified. PreparedLocalExecutionServer.is_qualified() verifies
the current process, safe server configuration, actual getsockname/SO_ACCEPTCONN and
the owned Uvicorn server's live serving socket/descriptor after handoff. It rejects
closed, unstarted, stopped, wrong/multiple sockets, reload/workers/proxy mutation or
forked ownership. A future supervisor must start that exact server using
server.serve(sockets=[prepared.socket]), own the context until HTTP shutdown and pass
the same private preparation object to admission. No server.serve/run call or workflow
runner is implemented here. Tests exercise bare Uvicorn HTTP startup/shutdown only,
with an empty FastAPI app and no provider/database/workflow lifespan.

Never derive this evidence from ASGI scope['server'], Host, Origin, a public bool or
a Settings flag. Scope['app'] identity must also match the preparation owner. Request
scope alone cannot prove listener binding, worker count or proxy configuration.
Ordinary existing startup has no private qualification object, so admission fails
closed with environment_unverified rather than inventing an ASGI proof.

Execution request checks reuse the unchanged M5F-2 peer/Host/Origin/forwarding policy
and additionally require POST with exactly one application/json Content-Type (optional
`; charset=utf-8`). This rejects forms, simple text and ambiguous/duplicate media types.
Verified literal IPv4/IPv6/mapped loopback peers are still mandatory; neither browser
headers nor Content-Type authenticate a user. Cross-origin/opaque origins, untrusted
hosts, Forwarded/X-Forwarded-*/X-Real-IP and invalid Sec-Fetch-Site remain denied. Local
CLI requests without Origin still need the socket-peer and owned-server checks.

No proxy, container, tunnel, public or multi-user execution is supported. A loopback
proxy that strips identity headers cannot be distinguished from a direct local peer.
Do not route one app through additional listeners or strip/replace identity middleware.
Qualification describes the owned server, not every process/interface on the machine.
Trusted composition must exclusively own that server/app and require one application
instance. Public deployment needs a separate authenticated design; legacy PromptRequest
policy is unchanged, and CORS is not authorization.

## Resource/runtime bounds

| Resource | Local admission bound |
| --- | --- |
| Active/reserved workflows | 1 |
| Waiting/queued workflows | 0 (immediate rejection) |
| Concurrent provider calls | 1, through the existing sequential optimizer |
| Evaluations | existing maximum 3 |
| Agent operations | at most 6 (1 create, up to 2 revisions, up to 3 Critics) |
| Gateway attempts | existing cap 4 per operation; at most 24 per workflow |
| Transport retries / JSON repair | existing caps 2 / 1, sharing that attempt budget |
| Request timeout / gateway operation deadline | at most 20 / 90 seconds locally |
| Stored optimizer deadline | >0 and <=300 seconds |
| Future lease / graceful shutdown period | 120 / 30 seconds |

These limits do not modify gateway retries, optimizer scoring/selection or the database
model. Admission rejects larger server gateway budgets. Future creation must set the
bounded immutable optimizer timeout. The existing output-token cap is 8192 per model
request; attempts/elapsed/output allowances do not promise a monetary or input-token
budget. Unknown token/cost observations remain unknown.
Provider concurrency bounds local dispatch, not remote work that may continue after
cancellation or a lost response; an in-process gate cannot observe or prove its end.

qualify_execution_runtime() queries sqlite_version() on separate real sqlite3 and
aiosqlite in-memory connections and applies the unchanged require_safe_sqlite_wal()
policy to both; Python must be the qualified 3.12.16. It opens no configured database.
Use only that helper's process-owned observations in production composition, never
client/package strings or constructed test observations. Unknown/unsafe drivers fail
closed. Actual future WAL connections must still pass the existing writer gate before
any journal change. No driver replacement, policy exception or DELETE fallback exists.
Do not initialize a writer while flags are off, while API-only, or during ordinary
startup; qualification/admission is not permission to provision the development DB.

Capacity is thread-safe only within one gate/process, not across separate processes,
gate instances or machines. PID checks reject inherited ownership but do not discover
other independently launched instances. Require one process/instance and one shared
application-scoped gate until a stronger coordinator is reviewed. Fencing still
protects individual durable claims; it does not enforce a global concurrency budget.

## Required M5F-3B supervision and remaining blockers

No application currently owns the preparation, runtime observations or admission gate.
M5F-3B must wire the exclusive local startup owner, lifecycle and HTTP admission path
together before any execution may be activated. A normal `uvicorn app.main:app` command,
an ASGI address or flags alone must never provide a permissive fallback.

The future application supervisor must hold strong references to bounded worker tasks,
collect every exception and release local slots only after worker cleanup/assessment.
It must distinguish in-memory admission from acknowledged persistent claim and define
the ambiguous 202/admission crash window. Request disconnect must not cancel a worker.
After database claim, reservation/staging/evaluation/finalization remain the existing
short service transactions, closed before provider awaits. Never add API/runner retries
around paid calls or hold a DB session while generating.

At shutdown: close admission atomically, drain within 30 seconds, cancel and await
remaining workers under an explicit cancellation-cleanup bound, then close provider,
read/write engines and the owned listener. Do not equate local slot release with a
terminal DB transition. Between-operation renewal must remain inside a live 120-second
lease (90-second gateway deadline plus margin); never renew after expiry. Interrupted
provider or commit acknowledgments stay unknown, previously committed evaluations
survive, and the existing read-only recovery service grants no replay/mutation authority.

An in-process runner can supervise live tasks and bound local resources. It cannot
prove exactly-once billing, durable queued delivery, receipt acknowledgment after
crash, stopped former workers across hosts or safe provider replay. Durable queue/
admission records, multi-process coordination and authenticated/public deployment
remain separate designs. No such guarantee is claimed by M5F-3A.
