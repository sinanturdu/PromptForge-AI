# Local MVP acceptance and release readiness — M5F-5B

Status: documentation/local qualification package for review, **not a released or
generally production-ready application**. M5F-5C must independently qualify Linux/CI,
review the PR/integration and authorize any merge/release action.

## Baseline and documentation gaps

Inspection on 2026-11-06 started with a clean tree on `feature/m5f-workflow-api`,
HEAD `ae19cd23a7eea94333b8b95f50cb644a823872d8`. Repository root matched the requested project.
The [M5F-5A audit](m5f-security-regression-audit.md) is prior evidence, not this run.

| Gap identified before edits | Correction / authoritative guide |
| --- | --- |
| Root/backend readmes and architecture still label implemented workflow/persistence/UI as planned | Current entry points and architecture inventory; historical ADRs distinguished from implementation |
| Unpinned/implicit Python selection, broad migrations against default DB, private paths as only startup example | [PowerShell setup](local-mvp-setup.md), explicit executable and new dedicated target |
| Unused APP_HOST/APP_PORT examples, outdated execution/provider comments, ambiguous Vite origin advice | Corrected examples and [exact configuration/mode reference](local-mvp-operations.md) |
| No complete operator lifecycle, safe maintenance, cost or security reference | Operations guide reuses audited bounds; no new deployment support |
| Missing troubleshooting and practical acceptance evidence | [Troubleshooting](local-mvp-troubleshooting.md), this checklist and independent qualification |
| Stale frontend counts, missing remount-safe revocation explanation, inconsistent future-work claims | Current UI guide corrections; dated results separated from historical audit |

## Qualification evidence

All results in this section are independently executed for M5F-5B on Windows on
2026-11-06 unless marked otherwise. Counts are observations of this revision, not
perpetual guarantees. Tools: Python 3.12.16, connected SQLite 3.53.1 through sqlite3
and aiosqlite, uv 0.13.6, Node 24.11.1, npm 11.6.2 and PowerShell 7.6.9. No real LLM call or charge.

| Item | Classification | Evidence |
| --- | --- | --- |
| Repository/branch/starting commit/clean tree | Verified | Expected branch and `ae19cd2` |
| Python and connected SQLite | Verified | Existing private interpreter and newly created disposable `.venv` both pass `app.db.verify_ci_runtime` |
| Frozen backend installation | Verified | Fresh disposable source copy, `uv sync --frozen --offline --no-python-downloads --python` with approved interpreter; 42 cached packages |
| Frozen frontend installation | Verified | Same disposable copy, `npm ci --offline --no-audit --no-fund`; 299 packages from cache |
| New DB/migrations | Verified | Only disposable file; Alembic head `0002_workflow_persistence`, five domain tables, `integrity_check=ok`, empty `foreign_key_check` |
| Independent frontend build | Verified | Built installed disposable copy plus normal checkout build; no source/secret directory used as SPA root |
| Documented CLI modes | Verified | Real child processes: disabled GET/POST 404, API-only create 405/start 404, ordinary both-flags create 503 |
| Dedicated same-origin CLI | Verified | Built SPA and deep routes 200, OpenAPI remains JSON, unknown API/private paths 404; mismatched Origin/Forwarded rejected 403 |
| Stock fake behavior | Verified | Explicit save/create/start through dedicated CLI; 202 followed by FAILED with empty fake script, not a successful generated result |
| Full backend pytest | Verified | 1658 passed, one known dependency warning; no skipped/xfail cases |
| Strict mypy | Verified | `app tests migrations benchmarks`: 137 files, no issues |
| Ruff lint / formatting | Verified | Passed; 141 backend files already formatted |
| Full frontend Vitest | Verified | 192 passed in 9 files; no skips |
| TypeScript / ESLint | Verified | Both passed |
| Full Chromium | Verified | 34 passed; one worker, zero retries, no skips |
| Documentation links/paths/commands | Verified | 204 local links in 48 Markdown files pass; 23 PowerShell blocks parse; actual startup/install/migration commands independently exercised as described |
| Documentation helper | Verified | Standard-library link checker passes strict mypy, Ruff lint and format checks separately |
| Whitespace and repository hygiene | Verified | Tracked/new-file checks pass; only env examples tracked, no DB/sidecar/build/report/real env additions; secret-marker scan of modified/new docs/helper passes |
| Development DB fingerprint | Verified | SHA-256, size and modification ticks identical before/after; WAL/SHM absent; no connection opened by qualification |
| Cleanup | Verified | Disposable installed copies removed after child exit; no test listeners/processes, E2E DB directories or qualification directories remain |
| Fresh OS/prerequisite/managed-Python/browser download | Not verified | Existing approved interpreter, cached packages and installed Chromium used; no uncached download or new-machine claim |
| Manual human UI acceptance / Ctrl+C on documented CLI | Not verified | Automated UI/lifecycle evidence is separate; command probe terminates only owned children |
| Linux connected runtime / GitHub Actions | Not verified; release gate | Existing workflow config is inspected, not executed remotely |
| Cost-bearing real-provider smoke / real-model quality | Not verified | No live API call authorized/performed; fake contracts do not establish semantic quality |
| No authentication, cross-tab guarantee, flood protection or exactly-once remote billing | Known limitation | Accepted only within the documented trusted-local scope; not public deployment support |
| Unsafe/unavailable exact Python/SQLite build on a new machine | Release blocker for that installation | Stop at preflight; obtaining a reviewed passing build is required, never bypassed |
| Final release approval and Linux/CI qualification | Release blocker for overall MVP release | M5F-5C review and qualification are incomplete; this package alone cannot release/merge |
| Public/multi-user/proxy/multi-worker deployment | Not applicable to local qualification | Unsupported, no authentication; no production-readiness claim |
| PR/merge/release tag | Not verified; deferred | No stage, commit, push, PR, merge, branch switch or tag in this phase |

Warnings: the backend reports one Starlette/httpx TestClient deprecation. The disposable
build emitted a Tailwind/Vite plugin timing diagnostic without failing. uv's general
interpreter inventory encountered a broken unrelated Python 3.14 PATH entry; every
qualification command instead used the explicit approved 3.12.16 executable. Chromium's
existing NO_COLOR/FORCE_COLOR warning did not fail the 34-test run. An extra mypy
invocation from repository root incorrectly resolved the directory arguments and
reported `Duplicate module named "__main__"`; rerunning from documented `backend/`
passed all 137 source files. No setting was weakened or directory removed.

The fresh-copy source/manifests were taken from this reviewed starting tree, with no
`.env`, node_modules, `.venv` or development DB copied. Cached dependency installation
was reproduced; obtaining a trusted safe interpreter on a fresh machine remains a
prerequisite. The repository does not distribute the private Windows Python build.
The no-generation provider-construction command was also run successfully in fake mode.
CLI probes were checked in a disposable copy at a different absolute path and dedicated
qualification ports; no real env file or original environment was overwritten.

Development database SHA-256, size and modification time were identical before and
after qualification. `backend/data/promptforge.db-wal` and `-shm` were absent both
times. Concrete local fingerprints are retained in private local verification
records outside version-controlled documentation. This is filesystem evidence of
non-mutation, not a new SQL integrity check on personal data. SQL integrity checks
ran only on the new disposable migrated database.

## Repeatable automated acceptance

Use [all check commands](local-mvp-setup.md#automated-checks) and the qualified exact
interpreter. Backend tests forbid external provider connections and guard the actual
development DB fingerprint; Chromium fixtures create migrated disposable databases.

| Capability | Automated evidence to inspect |
| --- | --- |
| Startup, prompt creation/preferences, persistence and reopening | Chromium `smoke.spec.ts`, `prompt-workflow.spec.ts`, backend PromptRequest tests |
| Search, filters, pagination and URL navigation | `history-filters.spec.ts`, `PromptHistory.test.tsx` |
| Round choices 1/2/3, explicit creation, stable-key replay, duplicate protection, no auto-start | `OptimizationPanel.test.tsx`, `optimization-ui.spec.ts`, workflow HTTP/admission tests |
| Separate start, acceptance vs completion, admission/capacity and unknown-start locks | Creation UI tests, `workflow-progress.spec.ts`, supervisor/HTTP tests |
| Controlled polling, visibility/pause, terminal/error stopping, refresh and selection | `WorkflowProgress.test.tsx`, progress Chromium cases |
| Persisted selection, quality vs completion, null/zero/decimal and all criteria | `WorkflowResults.test.tsx`, `workflow-results.spec.ts` |
| On-demand content, exact comparison/copy, failures/clarification | Result component/Chromium cases; no invented version or score |
| Integrity revocation across 200/409, close/reopen, parent remount, switching and reload | Result component/Chromium regressions from M5F-5A P1 follow-up |
| Cancellation/error/timeout/unrelated GET cannot restore approval; explicit fresh result can | Result component cases, real-browser manual revalidation path |
| Recovery read-only, exact committed proof refuses invalid graph, no replay | Backend readback/recovery and persistent coordinator cases |
| Listener/same-origin/forwarded/private-path protections | Backend local SPA/admission/HTTP tests and `same-origin.spec.ts` |

These are evidence pointers, not a claim that a developer has manually completed the
following checklist. Inspect failing reports; do not use retries/skips to manufacture acceptance.

## Optional manual operator acceptance

Use only a dedicated disposable DB and synthetic prompts. The stock fake deliberately
cannot demonstrate successful optimization. For deterministic successful scenarios,
run the existing scripted E2E fixtures. Any real-provider exercise needs separate
explicit approval and may cost money. Leave boxes unchecked until actually performed.

- [ ] Follow setup from a reviewed checkout; record exact executable and connected versions.
- [ ] Open the built same-origin app, save a synthetic prompt/preferences, reopen from history and reload.
- [ ] Select a supported round limit; create explicitly; verify stable-key duplicate handling and no auto-start.
- [ ] Start separately only in the approved test context; distinguish 202 from persisted completion.
- [ ] Observe active progress, pause/hidden stopping, terminal stopping, safe reload and workflow selection.
- [ ] Inspect backend quality separately from completion; verify null is not zero and all persisted criteria are displayed.
- [ ] Load content explicitly, compare exact source/selected text and copy only currently verified rendered content.
- [ ] Use only controlled test fixtures to exercise FAILED, clarification and integrity revocation; never corrupt retained data.
- [ ] Confirm read failures/unknown starts never trigger another execution and recovery stays read-only.
- [ ] Stop with Ctrl+C, observe cleanup, then inspect the owned DB/resources without altering records.

## Documentation and repository verification

From root with the qualified interpreter assigned as `$backendPython`:

```powershell
& $backendPython scripts/check_docs.py
if ($LASTEXITCODE -ne 0) { throw 'Local Markdown links failed.' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Git whitespace failed.' }
git status --short
git ls-files '*.env*' '*.db*' '*.sqlite*'
```

The stdlib helper checks tracked and unignored new Markdown local file targets and
heading fragments, excluding fenced examples. It does not fetch external links or
replace a full Markdown linter; none is configured. New untracked files also need
whitespace inspection (`git diff --no-index --check` against an empty file/NUL).
Review documented commands/paths against code; a link check cannot prove a command safe.
Check ignored artifacts with `git check-ignore`, and review examples/diffs for secrets.

Fingerprint the actual development DB before/after without connecting to it: SHA-256,
size, LastWriteTimeUtc ticks and existence of `-wal`/`-shm`. No migration, backup, restore
or runtime probe may open that DB during qualification. After test processes stop,
verify owned ports/processes/temp directories and sidecars are gone; never delete
unidentified resources or sidecars from a personal database.

## Release decision and M5F-5C handoff

### M5F-5C-1 CI preparation

The [CI qualification report](ci-qualification.md) records the later clean baseline
`e48907c`, four prepared jobs, explicit Ubuntu/Python/uv/Node selection, immutable
action references, connected SQLite/WAL preflight, fake-provider isolation and
content-free failure artifact policy. It also records new independently executed
Windows results and any failures separately from this M5F-5B evidence.
Linux/remote qualification remains pending; local Linux execution is blocked by
the unavailable supported test environment. Review the report's actual PASS/FAIL/
PENDING/BLOCKED classifications before any approved push/PR. All four named checks
must ultimately pass on the relevant revision; final release/merge is still separate.

No new P0/P1 behavior issue is identified by the completed evidence; this is not
proof of absence. Any newly confirmed P0/P1 requiring behavior/architecture changes
blocks this package and requires review. Known security limits are documented, not fixed
by adding hosting support here.

Before release, M5F-5C must qualify the actual Linux Python/SQLite/browser environment,
run the existing CI jobs and independently review any failures; review the complete
documentation/implementation diff and prerequisite reproducibility gap; perform the
authorized PR/integration/merge process and decide version/tag/release scope. Do not
call the feature branch merged, remote CI green or the MVP release complete before then.

## Changed files and final Git state

This section records M5F-5B's completion snapshot. The CI report above records the
later M5F-5C-1 changes and qualification; the historical inventory below is preserved.

Documentation/configuration examples only, plus one standard-library verification helper:

- [Root README](../README.md).
- [Backend README](../backend/README.md).
- [Frontend README](../frontend/README.md).
- [Backend environment example](../backend/.env.example).
- [Frontend environment example](../frontend/.env.example).
- [LLM README](../backend/app/llm/README.md).
- [Current architecture](architecture.md).
- [ADR index](adr/README.md).
- [ADR-005 clarification](adr/ADR-005-in-process-task-runner.md).
- [Supervisor guide](execution-supervisor.md).
- [Workflow API contracts guide](workflow-api-contracts.md).
- [Frontend integration guide](frontend-workflow-integration.md).
- [Frontend creation guide](frontend-workflow-creation.md).
- [Frontend results guide](frontend-workflow-results.md).
- [Local setup guide](local-mvp-setup.md) (new).
- [Local operations/configuration/security guide](local-mvp-operations.md) (new).
- [Local troubleshooting guide](local-mvp-troubleshooting.md) (new).
- [This acceptance/release checklist](local-mvp-release-checklist.md) (new).
- [Documentation checker](../scripts/check_docs.py) (new).

The original M5F-5A audit remains unchanged as historical evidence. No production code,
tests, APIs, models, migrations, scoring, fencing, dependencies/locks, default runtime
or CI workflow changes. Branch/HEAD remain `feature/m5f-workflow-api` /
`ae19cd23a7eea94333b8b95f50cb644a823872d8`. Final scope is 14 modified tracked files and
five untracked new files, all unstaged. No commit/push/PR/merge/tag/branch switch occurs.
