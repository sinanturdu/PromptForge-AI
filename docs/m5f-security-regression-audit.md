# M5F-5A security, regression and reliability audit

Status: ready for M5F-5A code review after the corrections below. This is a local MVP assessment,
not production certification, a formal proof or an exactly-once billing guarantee.

## Baseline and scope

- Repository: `C:\Users\sinan\Projects\PromptForge-AI`.
- Branch: `feature/m5f-workflow-api`; clean initial tree at
  `6572f160255a453426930b8b107872c01c48fe9c`.
- Reviewed M5E base `94464b7` and all nine M5F commits through `6572f16`:
  `3b1ba2e`, `a4ad337`, `bb2d9d6`, `5a9280c`, `9344331`, `35a3af8`,
  `b370b4d`, `dfc6e4c`, `6572f16`. No Git history changes are authorized.
- Qualified private interpreter:
  `C:\Users\sinan\AppData\Local\PromptForge\sqlite-qualification\backend-venv\Scripts\python.exe`.
  Real in-memory sqlite3 and aiosqlite connections both report SQLite 3.53.1;
  Python reports 3.12.16. Runtime preflight passed.
- Before testing, elevated read-only Windows listener/process inspection found no
  listeners on 8000, 5173, 15173 or 18080 and no repository-related Python/Node tests.
  The initial restricted CIM query was denied; elevated inspection succeeded.
- Development database fingerprint: 106496 bytes; LastWriteTimeUtc ticks
  `639293924815250437`; SHA-256
  `670DB6FD42689BE4F9B4CC24D0715042187B57743526A951CCFCC87797C04E10`.
  WAL/SHM absent. Tests use migrated disposable databases, not this database.

Audited configuration/manifests/CI, ordinary save/read APIs, workflow admission and
HTTP routes, actual listener qualification, supervisor/guard/gateway lifecycle,
M5E transactions/persistence/readback/recovery, static SPA security, frontend
attempts/client/query observers, progress, result/history/comparison/copy and tests.

## Threat and failure assumptions

The supported environment is one trusted local operator, trusted application/build
files, one process/worker, a directly owned loopback socket and a qualified SQLite
runtime. Remote peers, forged Host/Origin/forwarding headers, malformed requests,
lost responses, stale browser state, cancellation, changed fences, missing/corrupt
records and uncooperative asynchronous providers are considered. A malicious local
administrator, compromised provider/server/build, remote billing semantics and a
CPU-blocked/dead event loop cannot be solved by this in-process boundary.

## Findings inventory (established before production edits)

| ID / severity | Affected files and reproduction | Impact / resolution |
| --- | --- | --- |
| F1 / P1 | `WorkflowHistoryContent.tsx`, `WorkflowResults.tsx`: load a valid result, then request content history returning 200 with integrity findings or 409 `workflow_integrity_inconsistent`, while ordinary progress/metadata caches remain older and valid | The child rejected history, but the parent still offered the old prompt and copy action. Both reproducing cases failed before correction. The initial component-local correction retained the observation after the child unmounted, but did not survive the entire result view remounting; F6 supersedes that lifetime mechanism. Actual metadata/records remain available. |
| F2 / P2 | Supervisor retry, HTTP creation acknowledgment, static-path coverage | Added three real-coordinator cases proving a retry rechecks fence/lease/shutdown authority, two before/after-commit lost-acknowledgment cases proving stable-key replay, and seven encoded/private-path cases against actual private fixture files. No execution or transport policy changes. |
| F3 / P2 | `PromptHistory.test.tsx`: baseline full Vitest run fails at page-2 navigation; isolated reproduction passes | Existing test can retain the old pagination element while a debounced filter request replaces the list. Synchronize the requested filter and current ready pagination before clicking. Preserve URL/data assertions; no timeout increase, retry or product change. |
| F4 / P3 | Documented operational limits | Session storage is per tab; no cross-tab coordinator. Gateway retry budgets are not one HTTP attempt per AgentRun. Read snapshots are not remote-call liveness. Linux runtime qualification/CI still requires its own run. Keep these limitations explicit. |
| F5 / P2 | `backend/app/db/session.py`: ordinary PromptRequest engine with `echo=True` logs a synthetic private bind value (reproduced with an in-memory database) | Enable SQLAlchemy `hide_parameters=True`, matching the existing workflow read/write engines. Retain SQL diagnostics while hiding bound prompt values and parameter-bearing error representations. No SQL/transaction/model changes. |
| F6 / P1 | Independent review follow-up: close/reopen the entire results view after content-integrity revocation while its old successful result Query remains cached | Component-local state reset on remount allowed cached approval to reappear without a new result GET. Both 200/409 component reproductions and the real Chromium retained-cache reproduction failed before this correction. Revocation now lives in a shared in-memory QueryClient record scoped to the exact parent/workflow pair; view remounts and workflow switching cannot clear it. Only explicit fresh validated result revalidation can recover. |

No confirmed P0 finding. F6 corrects the independently discovered gap in the initial
F1 implementation; follow-up verification is recorded separately below. F5 and the
F2/F3 coverage and synchronization changes are preserved.

## Independent-review P1 follow-up: remount-safe result revocation

The original `contentIntegrityFailed` React state was owned by `WorkflowResults`.
Closing that view destroyed the observation, while `staleTime: Infinity` and
`refetchOnMount: false` permitted an older successful terminal result Query to be
reused. Child-only unmount coverage did not establish safety across parent remounts.

Before correction, two new component cases (200 integrity findings and HTTP 409)
failed when the copy action reappeared on reopen. A new real Chromium case also
failed because selected text reappeared without a fresh result GET. Component tests
retain the cached result with a disabled QueryObserver; Chromium pauses browser
cache-collection timers across close/reopen. These deterministically exercise the
retained-cache window without sleeps, retries, skipped assertions or extra execution.

`resultIntegrity.ts` stores only a revoked flag and monotonic observation revision
under a normalized PromptRequest/workflow key in the existing shared QueryClient.
The record has infinite garbage-collection lifetime, no network query function and
no polling. Synchronous cache subscription withdraws parent approval before paint.
It survives results/parent unmounts and navigation; another workflow's successful
GET cannot clear it. No prompt, result or Critic text is stored in this record or
in browser storage. The record lasts for the application QueryClient session; a
browser reload discards both private cached approvals and guards and queries the
backend again. This is not cross-tab or disk-persistent state.

Manual `Sonucu Yenile` still uses the existing terminal-result API and response,
parent/workflow/version/evaluation/selection validation. Recovery requires a new
successful Query data update after the explicit request, plus the same captured
integrity observation. The existing `dataUpdateCount` defense rejects cancellation
that returns reverted cached data; observation identity also rejects a success that
started before a newer integrity finding. Pending, failed, timed-out, malformed,
canceled, progress, metadata and other-workflow reads do not clear revocation.
There is no automatic recovery or new execution POST. Recovery closes the old
content panel and removes its inconsistent cache; the next content load is explicit
and fetches a new snapshot, which may revoke approval again.

The browser case covers retained cache, close/reopen, switching away and back,
metadata/progress reads, manual recovery, a second integrity observation and reload.
Its fixture restores the known selection pointer only in its owned disposable
database to simulate an external operator repair; this is not product repair logic
or a new endpoint. The pending alternate workflow is never executed. Browser
execution POST/provider counts remain unchanged, and logs/storage are checked for
the synthetic prompt sentinel. Component cases additionally cover full parent
remount, cancellation, timeout, invalid/failed responses, unrelated successful result
reads, identity scoping and retained bad-history cache eviction.

Follow-up changes are limited to eight files:

- `frontend/src/features/workflows/resultIntegrity.ts` (new scoped in-memory guard).
- `frontend/src/features/workflows/queries.ts` (scoped integrity key).
- `frontend/src/features/workflows/components/WorkflowResults.tsx` (guard and recovery).
- `frontend/src/features/workflows/components/WorkflowHistoryContent.tsx` (revocation callback).
- `frontend/src/test/WorkflowResults.test.tsx` (12 additional component cases).
- `frontend/e2e/workflow-results.spec.ts` (one additional real-browser case).
- `frontend/e2e/local-spa-fixture.ts` (disposable fixture selection restoration).
- This audit report.

No backend, dependency, lockfile, production database or API change is made in this
follow-up. The existing staged M5F-5A backend changes are retained exactly; backend
tests were not rerun for this frontend-only follow-up. Their original results remain
in the separate original-audit verification table below.

Final follow-up verification (Windows, installed dependencies, fake providers):

| Verification | Result |
| --- | --- |
| Before-fix component reproduction | Both retained-cache close/reopen cases failed (200 findings and HTTP 409) |
| Before-fix Chromium reproduction | Failed: old selected text reappeared without a new result GET |
| Focused WorkflowResults Vitest | 56 passed; includes all 44 pre-follow-up cases and 12 new cases |
| Full frontend Vitest | 192 passed in 9 files; no skips |
| Focused Chromium results E2E | 10 passed; one worker, zero retries |
| Full Chromium E2E | 34 passed; one worker, zero retries; no skips |
| TypeScript / ESLint / production build | Passed / passed / passed |
| Connected runtime qualification | Python 3.12.16; sqlite3 and aiosqlite each SQLite 3.53.1; passed |
| Git whitespace (working tree, existing index, new file) | Passed |
| Development database | Same SHA-256, 106496 bytes and modification ticks as baseline; WAL/SHM absent |
| Cleanup | No inspected test-port listeners, repository-related test processes or disposable E2E directories remain |

The focused browser case passed again in the full run. No assertions were relaxed,
no test retries or skips added, and no paid provider calls made. Chromium emitted
only the existing NO_COLOR/FORCE_COLOR environment warning. This follow-up was
verified locally on Windows; Linux/remote CI was not run.

Branch and HEAD remain `feature/m5f-workflow-api` / `6572f160255a453426930b8b107872c01c48fe9c`.
The 11 pre-existing staged files remain byte-for-byte staged as received: the Git
index SHA-256 is still `31BFE4A2FBEDEE2FADCC6AC2EE7E3B3ABE63F5D15B3265DF52D9CD7D16AA3411`.
This follow-up leaves seven modified working-tree files and one new untracked
guard module for review. It does not stage, commit, push, merge or switch branches.

## Authoritative workflow and execution bounds

Save creates only a PromptRequest. Explicit create uses a stable scoped key and
server-owned configuration. A dedicated short write transaction handles exact replay
or conflicting source/configuration/active-run rejection. Start resolves real ownership,
reads fresh integrity-checked state, atomically reserves one local slot and transfers
strong task ownership before 202. 202 is not a durable claim or completion.

The existing optimizer makes the fenced claim, renews only between operations,
reserves each operation before provider dispatch, persists staging/evaluation atomically
and finalizes through existing transactions. No provider await occurs inside a write
transaction. Receipt uncertainty permits one bounded persistence-only reconciliation,
never a repeat of the agent dispatch. Progress/result reads validate the stored graph;
recovery remains diagnostic and never authorizes replay or mutation.

| Resource | Verified implementation bound |
| --- | --- |
| Concurrent workflows / waiting queue | 1 / 0 |
| Concurrent local provider dispatch | 1 |
| Evaluation rounds / agent operations | 3 / 6 |
| Gateway attempts per operation / workflow | 4 / 24 |
| Outer deadline | 300 seconds |
| Confirmed lease / safety margin | 120 / 15 seconds |
| Graceful shutdown / cancellation cleanup | 30 / 10 seconds |
| Local request / gateway deadline maximum | 20 / 90 seconds |

Every actual attempt, including retries/repair, goes through the supervised provider's
fresh exact-fence snapshot. Guards also check confirmed lease, monotonic deadline,
environment and provider activity; cancellation bypasses ordinary gateway failure
conversion. Uncooperative work retains its slot/dependencies rather than releasing
them for a second workflow. Persisted unresolved records remain unresolved; no terminal
success/failure is invented to conceal uncertainty.

The existing gateway permits bounded transient retries (including TIMEOUT) and schema
repair. Thus one reserved AgentRun can authorize multiple HTTP attempts; an earlier
timed-out remote attempt may still have run or incurred cost. The audit does not equate
fenced workflow deduplication with exactly-once remote execution, and does not change
this approved retry policy or introduce recovery/replay of unresolved operations.

## Security, privacy and frontend boundaries

Both flags default off; API-only mode registers no execution POSTs. Ordinary Uvicorn
cannot activate the owner/writer. Dedicated startup requires an existing migrated
non-development database, qualified connected runtimes and the actual single owned
127.0.0.1/::1 listening socket. Header/ASGI addresses alone grant no listener authority.
Reload, multiple workers, proxy rewriting/forwarding, SSL/root-path/inherited/UDS
patterns are unsupported and fail qualification. The socket peer, exact allowed
Host/Origin/fetch-site and POST JSON checks remain enforced before mutation.

The SPA has explicit browser routes and contained asset paths, no blanket fallback;
API/OpenAPI/docs retain their own responses. Private/dot/map paths and traversal are
rejected. Static requests reuse transport/security checks and safe response headers.

Public progress/history exclude prompt content, secrets and fencing details. Explicit
result/content endpoints intentionally expose validated local-user content. Error
handlers/gateway logs use fixed categories and approved diagnostics; raw provider/SQL
exceptions are not attached to public errors. No sensitive response is browser-persisted.
The built bundle must contain no synthetic test secrets or private-runtime paths.
The built artifact scan found no test-secret sentinels, OpenAI key markers or private
interpreter paths. Ordinary SQL diagnostics now hide bind values too (F5); SQL text
remains visible. Raw exception objects/SQL literals are not safe logging interfaces.

Frontend start markers are written before dispatch and survive reload; PENDING GET,
read failure or elapsed browser time does not unlock unknown/accepted starts. Queries
are keyed by parent/workflow, consume cancellation, and discard out-of-order selections.
Only visible active progress owns the two-second polling interval; GET deadlines are
ten seconds, errors stop automatic traffic and inspection never submits POSTs.

Results use backend-selected versions/quality, exact decimal strings, nullable scores
and all nine supplied criteria without rescoring. Text/code/markup remains plain text.
Comparison is scoped to approved stored versions; copying exports only exact validated
rendered text. Integrity observations must override every cached approval path (F1).

## Verification evidence

Baseline (before production changes): full Chromium 32 passed; initial full Vitest
177 passed / 1 navigation failure (F3); isolated F3 test passed. Full backend 1645 passed
with one Starlette/httpx deprecation warning. No unexpected skips.
F1 reproducer: both content-response cases fail because the copy button remains enabled.
F5 reproducer: the synthetic bind value appears in SQL diagnostics before correction;
the corrected SQL/ordinary logging suite passes all 15 tests, retaining statement logs.
Focused backend results: 481 admission/supervisor/HTTP/persistence/readback/recovery/SPA
cases passed, followed by 116 final supervisor/HTTP/SPA cases after adding acknowledgment
and encoded-path checks. Both have the same one dependency deprecation warning.
Corrected full frontend suite: 180 passed. TypeScript, ESLint and production build pass.
Strict mypy covers 137 files; Ruff check and formatting pass (141 files formatted).
The corrected navigation case passed three additional independent targeted runs under
the other regression workloads. Its original URL/page/content assertions remain intact.
Targeted `-t`/`-k` selections intentionally deselect other cases; final full suites must
have no unexpected skips. No skip/xfail/todo/only declarations were found in the test
trees.

Original M5F-5A verification results before the independent-review follow-up:

| Verification | Result |
| --- | --- |
| Full backend pytest | 1658 passed; 1 warning; no skipped/xfail cases |
| Full frontend Vitest | 180 passed in 9 files; no skips |
| Full Chromium | 33 passed, one worker, zero retries |
| Strict mypy app/tests/migrations/benchmarks | 137 source files, no issues |
| Ruff lint / formatting | Passed / 141 files already formatted |
| TypeScript / ESLint / production build | Passed / passed without warnings / passed |
| Git whitespace, tracked and added files | Passed |
| Development database | SHA-256, size and modification time identical to baseline; WAL/SHM absent |
| Cleanup | No test listeners on the inspected ports, repository-tagged test processes or `promptforge-*e2e-*` directories remain |

The backend warning is `StarletteDeprecationWarning` from `fastapi.testclient` about
httpx/httpx2. It is unrelated to these changes; dependencies were not changed. Chromium
also reports the environment's NO_COLOR/FORCE_COLOR conflict, without test failures.
No unhandled-Promise/task-resource warnings or unexpected skipped tests were observed.
No paid provider calls or operations on the real development database ran. Migrations
and intentional integrity-corruption probes operated only on owned disposable databases;
no automatic repair or recovery execution was introduced.

Commands use installed dependencies, frozen locks and the private interpreter above:
`python -m app.db.verify_ci_runtime`, `python -m pytest -q`, focused pytest files,
`python -m mypy app tests migrations benchmarks`, `python -m ruff check .`,
`python -m ruff format --check .`, frontend `npm run typecheck`, `npm run lint`,
`npm run test`, `npm run build`, and `npm run test:e2e` with
`PROMPTFORGE_E2E_PYTHON` pointing to the qualified interpreter. Git whitespace checks
include tracked diffs and every new file via `git diff --no-index --check`.

Focused pytest command files were `test_execution_admission.py`,
`test_execution_supervisor.py`, `test_workflow_execution_api.py`,
`test_workflow_persistence.py`, `test_evaluation_persistence.py`,
`test_workflow_read_api.py`, `test_workflow_readback.py`, `test_workflow_recovery.py`
and `test_local_spa.py`; final supervisor/HTTP/SPA and database/logging runs followed.
Frontend focused runs covered WorkflowResults/PromptHistory, and the full frontend
suite retained all creation/progress/result tests. The full real-browser suite covers
three rounds, early stop, qualified/not-qualified/unassessable, failure, clarification,
unknown accepted starts, interrupted polling/reload and both progress/content integrity
revocation paths. The new content case verifies actual retained Evaluation records and
no extra execution/provider dispatch after corrupting only its owned disposable DB.

## Files changed

- Production: `backend/app/db/session.py` (one parameter-hiding option),
  `frontend/src/features/workflows/components/WorkflowHistoryContent.tsx`,
  `frontend/src/features/workflows/components/WorkflowResults.tsx` (F1 only).
- Backend tests: `backend/tests/test_database.py`, `test_execution_supervisor.py`,
  `test_workflow_execution_api.py`, `test_local_spa.py` (13 added parameterized cases).
- Frontend tests: `frontend/src/test/WorkflowResults.test.tsx` (two added cases with
  cancellation/error/fresh-read assertions), `PromptHistory.test.tsx` (synchronization).
- Browser tests: `frontend/e2e/workflow-results.spec.ts` (one added real integrity case).
- Added documentation: this audit report. No schema/model/migration, scoring/provider,
  admission/lease/retry policy, feature flag, dependency/lockfile or CI changes.

## Remaining limitations and deferred work

No cross-tab synchronization, durable task queue, automatic replay/recovery, public
deployment, proxy support or remote cancellation/billing guarantee is added. Operator
logging destinations and trusted local filesystem/build ownership remain deployment concerns.
Ordinary HTTP read concurrency is not rate-limited: NullPool snapshots bound each
request's graph/transaction lifetime, not the number of requests a hostile local process
can send. This is a trusted-local MVP boundary, not DoS protection or public hosting.
Linux connected runtime/remote GitHub Actions have not been executed in this Windows
audit; their qualification remains required before a remote-green claim. M5F-5B owns
release-readiness documentation; M5F-5C owns remote GitHub
CI/PR/merge qualification. Neither phase nor any Git mutation is performed here.

At original M5F-5A completion there were 10 modified files and this new report, all
unstaged. Before this follow-up, the user had staged those 11 files. This follow-up
preserves that existing index and adds only working-tree changes; final follow-up
status and verification are recorded above. No stage, commit, amend, push, merge,
PR or branch switch is performed by this follow-up.
