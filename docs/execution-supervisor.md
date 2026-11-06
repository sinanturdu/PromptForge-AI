# Supervised persistent execution — M5F-3B

## Activation boundary

WorkflowExecutionSupervisor owns one task/reservation, zero queued workflows and one
sequential provider-dispatch stream. It invokes the existing execute_persisted_optimizer
exactly once per accepted local task. No workflow POST route, workflow creation,
frontend activation, resume, takeover or new persistence transaction is introduced.
M5F-2 GETs and ordinary startup retain their existing behavior. M5F-3C separately adds
the [two guarded HTTP operations](workflow-api-contracts.md) using this shared owner;
the creation route borrows its existing persistence service and start calls submit.

Both feature flags remain false by default. Even with both true, ordinary
`uvicorn app.main:app` has no execution owner/supervisor/writer. The new keyword-only
create_app(execution_models=...) is an internal composition seam: models alone create
no supervisor and do not qualify a listener. Only the explicit local entry point
connects it to the owned server handoff. There is no provider call during startup.

## Explicit local startup

From backend/, using the approved private Python environment, an operator may explicitly
start the internal runner against an **existing, already migrated, non-development**
SQLite file:

```powershell
$taskPython = 'C:\Users\sinan\AppData\Local\PromptForge\sqlite-qualification\backend-venv\Scripts\python.exe'
$env:WORKFLOW_API_ENABLED = 'true'
$env:WORKFLOW_EXECUTION_ENABLED = 'true'
$env:LLM_PROVIDER = 'fake'
& $taskPython -m app.core.execution_startup --database 'C:\path\to\approved-existing.db' --architect-model fake --critic-model fake --host 127.0.0.1 --port 8000
```

This example is documentation, not a command executed against a user database by
this milestone. It supplies no default model/credential, migrates nothing and does
not submit work. Fake mode has no default output script, so successful offline
executions inject a scripted fake in tests. Real model choice/credentials remain
backend-owned and require separate user authorization; no live calls occur in tests.

The entry point accepts only 127.0.0.1/::1, has no reload/workers/proxy/inherited-FD
options, explicitly pins one Uvicorn worker and rejects absent feature opt-in. The
M5F-3A helper owns the exclusive socket. A narrow Uvicorn startup hook activates the
owner only **after** the original socket has been handed to the actual serving
server and is_qualified() succeeds. A prepared/unstarted socket, ASGI address,
unverified listener, reload, proxy configuration or multiple workers cannot activate
execution. Each submission rechecks ownership, request security and runtime.

ApplicationExecutionOwner qualifies actual sqlite3/aiosqlite memory connections, refuses
the default development database (including aliases/hard links), checks the explicit
existing file/read schema, then creates one lazy dedicated WAL writer. No startup
connection enables WAL or initializes a database. Actual write connections still pass
the unchanged connected SQLite gate and WAL/FULL/FK setup. Missing/unmigrated storage
fails closed. The provider/reader are borrowed from the existing application lifespan;
one additional guarded gateway and the existing agents reuse that provider.

Deployment remains one trusted local process/instance with no reverse proxy, container
publication, tunnel or additional app listener. Socket proof establishes the owned
listener, not the absence of independently launched processes or a loopback proxy
which strips headers. These unsupported deployments need separate authenticated and
cross-process coordination designs. Host/Origin/CORS never authenticate a user.

## Internal interface and task ownership

After qualified startup, application.state.workflow_execution_owner.supervisor provides:

- `await submit(AdmissionRequest, scope=scope)`: returns sanitized SubmissionResult.
  accepted=true means a strongly owned local task exists; it is not durable claim,
  external-operation acknowledgment, quality qualification or an HTTP 202 guarantee.
- `await wait_idle()`: returns a bounded, content-free last completion diagnostic.
  Cancelling this waiter never cancels the worker. Workflow status/history come only
  from M5E readback, not this diagnostic.
- `begin_shutdown()`: synchronously closes admission and new provider dispatch.
- `await shutdown()`: shields one owned cleanup task and returns a typed shutdown
  result; concurrent/repeated calls reuse it. A previously stuck owner can complete
  cleanup after its worker actually stops.

Submission checks flags/security/environment before reading, validates immutable input,
loads actual scoped readback, rechecks after that await, then uses the M5F-3A atomic
gate. No await separates the reservation, task creation, strong reference and callback.
Task-construction failure releases the slot and closes the unstarted coroutine.
One done callback retrieves every task result/exception, releases its identity-checked
reservation and clears active state only if it still owns that exact task. A stale
callback cannot release a replacement reservation or mark a newer task idle.

Concurrent identical submissions conflict or observe the already active persisted
state. A different eligible workflow at capacity gets the existing capacity decision;
nothing is queued. Completed, failed, expired, inconsistent or unresolved workflows
are not restarted. A submission racing shutdown is rejected before reservation after
its read returns. No raw result, prompt, fencing token, SQL/provider exception or
private traceback is stored in operational diagnostics.

## Lease and deadline safety

Between-operation renewal alone does not prove that a stalled awaited operation or
slow cancellation will stop before expiry. The supervisor therefore uses a watchdog
and fresh fence checks, without an independently racing lease heartbeat:

1. An optional synchronous on_state callback in the existing coordinator observes
   each confirmed claim/renewal/reservation/staging/evaluation receipt. Default callers
   use None and preserve their original behavior. The monitor issues no renewal and
   never changes a revision, token, score, round, retry or finalization decision.
2. The watchdog starts a monotonic 300-second total budget before claim/input loading
   and monitors the actual confirmed 120-second lease. M5E's smaller stored optimizer
   deadline still applies. The lease safe deadline is 15 seconds before expiry; the
   same lease cannot be extended by a backward wall-clock adjustment. A confirmed
   renewal with the actual new fence can extend the monitored deadline. Expired,
   replaced or stale ownership never revives it.
3. An owned event-loop timer checks the safe deadlines and environment at most once
   per second while any provider/persistence await is blocked, and checks the exact
   deadline when earlier. Forward wall-clock changes are also inspected. Every state
   receipt and actual provider start/return checks synchronously again.
4. Before **each actual provider attempt**, including gateway retries/repair, the
   guarded provider obtains an exact existing execution_snapshot under the current
   fence. That existing service transaction closes before network dispatch. A changed
   token/revision, unavailable check or lost lease stops authority without a paid
   retry. There is still no transaction held across a provider await.
5. A safety stop irreversibly closes admission, cancels the owned task and propagates
   cancellation past gateway retry/FAILED conversion. Late responses are discarded.
   An uncooperative task retains its slot and dependencies until it truly stops.
   No FAILED/COMPLETED row is invented for a lease, cancellation or acknowledgment
   uncertainty. Previously committed evidence survives for ordinary M5E recovery.

The existing optimizer enforces three evaluations/six agent operations; the gateway
retains its four attempts per operation and configured retry/repair bounds. The
provider view counts actual started attempts, caps them at 24, and rejects concurrent
local starts. It does not manufacture token/cost counters or claim remote concurrency
after cancellation. Default local gateway deadlines remain at most 90 seconds,
leaving lease/persistence margin; the watchdog handles delay beyond that assumption.

These are in-process cooperative safety checks, not a hard-real-time or remote-provider
guarantee. A stopped/CPU-blocked event loop cannot execute a timer; on resumption the
boundary checks refuse further dispatch. Outstanding SQLite/remote operations may
already have committed/completed, and only readback can establish their durable
evidence. No atomic transaction can cover a remote paid call and a fencing read.
Direct database tampering or extra processes are outside the one-owner deployment
contract. Exactly-once billing and upstream cancellation are not claimed.

## Shutdown and resource lifetime

The dedicated HTTP server closes admission **before** setting its signal-exit flag or
closing its listener, so listener
shutdown cannot be mistaken for an unexpected authority loss during drain. It starts
the owned shutdown task immediately, alongside bounded HTTP draining. The existing
lifespan then awaits that same task before disposing any borrowed resources.

Shutdown is irreversible: accepting → draining → cancelling if needed → stopped,
or stuck if cancellation does not finish. Thirty seconds allows the current in-flight
call to finish and persist its result; no new provider attempt starts during drain.
A final Critic call may therefore finish the workflow; an earlier-round interruption
can leave valid partial history and an unresolved reservation. After grace, cancel
and wait at most ten more seconds for cleanup. Test waiters drive these exact policy
deadlines with events rather than sleeping for 30 seconds.

If the task stops, callbacks collect its outcome, release its slot, and the owner
disposes the writer, then the application-owned provider/read/ordinary engines through
AsyncExitStack. Request/waiter cancellation does not abandon worker ownership. If
lifespan shutdown itself is cancelled, its resource stack is also retained.

If a task refuses cancellation, shutdown reports stuck instead of success. The
lifespan transfers the actual resource stack to the still-owned supervisor, reports
failure and retains provider/writer references; they are never closed under the live
task. No new submission is accepted. Process termination/operator intervention may
be necessary. If it later stops, another shutdown call safely completes retained
cleanup. Python cannot simultaneously force an arbitrary coroutine to stop, bound
process exit and safely dispose resources it still uses; process isolation would be
a separate package. A crash/forced exit can leave expired claims for read-only
recovery assessment, never automatic takeover/replay.

## Verification and next boundary

Tests use fake providers, real admission concurrency, owned Uvicorn socket handoff and
temporary migrated databases. Coverage includes three rounds, duplicate/capacity races,
failed claims/renewals, fresh-fence denial, lease extension/overrun, total deadline,
late responses, provider errors, before/after-commit failures, stale callbacks,
grace/cancellation/stuck shutdown, submission races and real disposal ordering.
Transaction checkout assertions prove provider dispatch starts outside DB transactions.
The development SQLite file and sidecars remain guarded and unchanged.

M5F-3C now provides creation/start POST routes and HTTP acceptance/idempotency semantics.
The owner exposes the same persistence service used by the supervisor only after
activation; request dependencies never dispose it. The supervisor's check_environment
is also used for creation authority, on the owning loop. No execution policy changes.
Frontend integration remains future work.
Linux deployment qualification and actual provider behavior must be verified in their
own approved environment; local fake tests prove sequencing/contracts, not live model
quality, power-loss durability or guaranteed remote cancellation.
