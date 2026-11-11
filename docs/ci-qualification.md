# CI foundation and Linux qualification — M5F-5C-1

The M5F-5C-1 observations below are historical. The M5F-5C-2 through M5F-5C-2B
sections record publication, hosted results and local corrections. They supersede
the earlier unpublished integration state.

This package prepares local CI configuration for review. **No push, PR, remote run,
merge or release is performed.** Windows command results, configuration inspection,
local Linux execution and GitHub-hosted execution are separate evidence levels.
The [release checklist](local-mvp-release-checklist.md) retains the prior M5F-5B observations.

## Baseline and existing integration state

Inspection on 2026-11-10 started clean at
`e48907cea6f2a0f5b6b22e0862fe3156260d1229`, branch `feature/m5f-workflow-api`.
`origin` is `sinanturdu/PromptForge-AI`. One workflow existed: `.github/workflows/ci.yml`,
with backend, frontend and Chromium jobs on `ubuntu-latest`, PRs to main and pushes
to main. It already selected exact managed Python 3.12.16 and frozen backend deps,
checked connected SQLite, and preserved full backend/frontend/browser suites.

Gaps: mutable action tags, unpinned uv/Node patch and runner label, implicit token
permissions, no job timeouts/concurrency, no docs/hygiene gate, and broad automatic
HTML/trace/screenshot artifact uploads. The latter could include rendered synthetic
prompt text and is replaced with a content-free failure status.

Read-only remote inspection found main at
`94464b7b1b2d3c1a2fbf41145cfa8a6f82fffe37`, matching local main; the feature branch
has 11 commits ahead and zero behind that base, no upstream and no remote branch
returned by exact-name search. Main's branch metadata reports `protected=false`.
The rulesets endpoint returned 403 (plan/public-repository restriction); full ruleset
configuration is not established by that partial observation. No settings were changed.

## Workflow architecture

The single existing workflow is extended; no duplicate workflow or install framework.
All jobs use explicit `ubuntu-24.04` hosted x86_64 runners and preserve the three
existing check names. A fixed runner label bounds the OS family; it does not freeze
every package/image revision.

| Job / check name | Gates | Timeout |
| --- | --- | --- |
| `Backend (Python 3.12 · Ruff · mypy · pytest)` | Frozen backend install, Linux/connected SQLite/WAL preflight, Ruff/format, strict mypy including migrations/benchmarks and scripts, full pytest | 20 minutes |
| `Frontend (Node 24 · TypeScript · ESLint · Vitest · Build)` | npm ci, typecheck, ESLint, full Vitest, production build | 10 minutes |
| `E2E (Chromium · isolated FastAPI + SQLite)` | Independently qualified backend, npm ci, locked Chromium/system deps, full suite/build, cleanup verification | 20 minutes |
| `Documentation and repository hygiene` | Exact isolated Python without backend deps, existing Markdown checker, small hygiene gate, committed comparison whitespace | 10 minutes |

Jobs own separate runner storage; no database, virtualenv or application state is shared.
Only normal uv/npm dependency caches are enabled; locks remain unchanged. Repeating
backend setup in the browser job is necessary for its independent runner. Documentation
installs no backend packages and disables caching. Existing npm pretest hooks build
the SPA for E2E; no backend/frontend build artifacts are transferred between jobs.

Triggers: PRs targeting main, pushes to main or this feature branch, and explicit
manual dispatch. Initial controlled feature push can therefore qualify before a PR.
PR checkout tests GitHub's merge revision, while a feature push tests its head; those
are distinct checks, not a claim that a head run proves merge integration. No path
filter can skip a security-critical check. Ref-scoped concurrency cancels superseded
runs; job/test failures remain failures, with no `continue-on-error` or test retries.
Manual dispatch requires the workflow definition on the default branch, per
[GitHub's event contract](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch).
The first controlled pre-merge qualification therefore relies on the feature-push
trigger, not an assumption that the new manual action is already registered remotely.

The docs job fetches comparison history without persisted credentials. Whitespace
compares PR base or push-before SHA with HEAD. Initial feature push/manual branch
dispatch uses merge-base with origin/main; manual main uses the preceding commit.
This checks committed changes rather than a trivially clean checkout diff. No Git
history is rewritten and comparison inputs are environment values, not shell-injected PR text.

## Pinned tools and supply chain

Full action SHAs were resolved from official release tags and their `action.yml`
files inspected. These actions use Node 24; an actual hosted runner/action handoff
still needs remote execution.

| Action | Reviewed version | Immutable reference |
| --- | --- | --- |
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| actions/setup-node | v7.1.0 | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| astral-sh/setup-uv | v10.3.3 | `68559e99cc807c1eb7e9238fd43a15bba460eec2` |
| actions/upload-artifact | v7.0.2 | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |

Node is explicitly 24.11.1/x64, matching the qualified local patch; uv is 0.13.6,
whose reviewed setup action includes that release's known checksums. There is no
latest-tool installer or `curl | bash`. Python installation is private to RUNNER_TEMP
with no bin installation and the explicit `cpython-3.12.16-linux-x86_64-gnu` request.
Find/sync require managed Python and disable fallback downloads. All backend commands
then use `.venv/bin/python`; E2E receives that absolute executable explicitly.

uv 0.13.6's [pinned managed download metadata](https://github.com/astral-sh/uv/blob/0.13.6/crates/uv-python-managed/download-metadata.json)
selects the [20261026 Python build](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026),
`cpython-3.12.16+20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`.
Its public archive SHA-256 is
`ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20`.
The metadata checksum matches the release asset digest; this is a public tool
checksum, not a private database fingerprint. This inspection does not execute Linux.
uv's [pinned download implementation](https://github.com/astral-sh/uv/blob/0.13.6/crates/uv-python-managed/src/downloads.rs)
hashes the archive and rejects a mismatch with that metadata; no external library
override or unchecked download is used as an alternative.

Application dependencies remain fixed by uv.lock (`--frozen`) and package-lock.json
(`npm ci`). `npx --no-install playwright install --with-deps chromium` uses the
already lock-installed Playwright package/revision, never an automatically fetched
latest Playwright CLI. Browser/system-dependency downloads retain their upstream
distribution trust; this is not a fully hermetic/offline Linux build.

Primary action references: [checkout](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1),
[setup-node](https://github.com/actions/setup-node/tree/949feb2413d6458794dcd2491c4babbbce0c15c1),
[setup-uv](https://github.com/astral-sh/setup-uv/tree/68559e99cc807c1eb7e9238fd43a15bba460eec2),
[upload-artifact](https://github.com/actions/upload-artifact/tree/cf430e030ddbb5b0abf93d22962f4752f3646cd9).

## Python, SQLite and Linux failure policy

The production guard is unchanged: exact Python 3.12.16 plus both **actual connected**
sqlite3/aiosqlite versions accepted by `require_safe_sqlite_wal()` (recognized SQLite
3.51.3+ or fixed 3.44.6/3.50.7 backports; unknown/affected versions refused). aiosqlite
wraps stdlib sqlite3 connections rather than selecting a second independent SQLite
distribution. Both connections are nevertheless opened and checked separately.

The upstream [SQLite build script at the selected build commit](https://github.com/astral-sh/python-build-standalone/blob/fa506e0858ac9f2cc5b289c137b888a2aa24d1ee/cpython-unix/build-sqlite.sh)
disables shared SQLite and builds `libsqlite3.a`. System sqlite3 CLI/package upgrades
therefore cannot be assumed to replace this interpreter's linked SQLite. The CI helper
also prints actual `_sqlite3` linkage with ldd, rather than inferring loaded libraries
from package versions. No custom SQLite compilation, system-library replacement,
driver substitution, version mock or LD_LIBRARY_PATH/LD_PRELOAD override is introduced.

`scripts/verify_ci_linux_runtime.py` requires Linux x86_64/64-bit Python, refuses
library-loading overrides and calls the existing exact-version/connected-driver
preflight. It rejects a non-thread-safe build or OMIT_WAL/OMIT_FOREIGN_KEY/OMIT_TRIGGER,
then uses the real dedicated writer in an owned temporary SQLite database to verify
WAL, FULL synchronous and foreign keys. The probe is disposed/removed before tests.
No application DB/env file/provider is opened by that helper. Full tests then exercise
actual migrated workflow storage; a CLI or metadata-only observation is insufficient.

If install, runtime/architecture/linkage, connected versions or the WAL probe fail,
backend/browser jobs stop before their suites. Preserve failure evidence, review the
exact interpreter artifact/loaded libraries and report a release blocker. Do not
swap a random library, loosen versions, skip tests or force DELETE mode. A substantial
runtime/driver change requires separate review.

Local Linux execution is **BLOCKED**: WSL listed only Docker Desktop's internal distro;
its docker CLI explicitly refuses execution there. No usable host Docker CLI or normal
Linux development distro was available on the inspected paths. No Docker/WSL service
was reconfigured or new Linux environment provisioned. The Linux artifact exists in
the pinned index; actual connected Linux versions and hosted behavior remain **PENDING**.
The earlier Windows SQLite 3.53.1 observation is not substituted for that evidence.

## Privacy, isolation and artifacts

Only `contents: read`; checkout credentials are not persisted. No PR-target workflow,
write token scope, repository/provider secret reference, deployment, tag or automatic
approval/merge. Flags remain false and `LLM_PROVIDER=fake`, `OPENAI_API_KEY` empty in CI.
Backend fixtures additionally remove real keys and reject non-loopback provider sockets;
SPA fixtures inject scripted fakes. Dependency/browser installation still needs network;
this does not claim a malicious PR has no network access or that live billing is safe.

The original fixtures retain loopback ownership, readiness identity checks, fresh
migrated per-test databases and finally-based cleanup. No existing server/development
DB is reused. Legacy fixture ports are 15173/18080; SPA fixtures own OS-assigned ports.
Full Chromium still discovers every existing spec with one worker and zero retries.
Post-run checks fail if owned temp directories or dedicated listeners remain; they
never kill an unidentified process or delete evidence to hide a cleanup failure.
Forced cancellation/runner loss cannot prove fixture teardown ran; hosted ephemeral
runner disposal is separate from normal finally/cleanup evidence.

HTML reports, traces, screenshots, raw logs and databases are **not uploaded**. On a
browser-test failure, only an allowlisted status and failed-or-unrun test count are serialized
to the three-day `playwright-status` artifact. Prompt text, IDs, URLs, errors, receipts
and token fields are omitted. Upload additionally requires successful sanitization
and its explicit created-file output flag; missing/malformed reports cannot upload
a pre-existing status-path file. They never change the failed test outcome. Detailed synthetic traces remain runner-local
for that job; reproduction remains the preferred diagnostic route.
The count follows Playwright's last-run ID list, which includes cases not run after
interruption; it is not mislabeled as an exact count of failed assertions.

`scripts/check_ci_hygiene.py` rejects tracked/unignored env secrets, database/sidecar,
key and generated-artifact filenames. Current operator readmes, local-MVP/CI guides
and env examples are checked for personal paths, common token/private-key markers
and literal private snapshot metadata without echoing matched values. Public tool
checksums and configuration names remain valid. This is not exhaustive content/history
secret scanning; historical reports are not rewritten and arbitrary source changes
still require review. Documentation links remain a separate read-only stdlib check,
not proof PowerShell installation commands or Linux runtime execute correctly.

## Findings and local evidence (2026-11-10)

- **P2, frontend test synchronization:** baseline full Vitest had 191 passes/one failure;
  the history Back/Forward test also failed alone at a detached `findByText` result.
  The unchanged row visibility assertion now runs inside the existing matched-query/
  ready-pagination wait. No product change, sleep, retry, timeout increase or assertion
  removal. All 23 history tests subsequently passed. The isolated `-t` reproducer
  intentionally deselected 22 other cases; no suite skip declaration was introduced.
- **P2 investigation, backend fixture timing:** initial full pytest had 1657 passes/one
  failure in `test_active_states_keep_unresolved_telemetry_unknown[3]`: its cancellation
  cleanup exceeded the fixture's five-second wait, with a SQLAlchemy cancellation log.
  That exact case passed alone (one dependency warning). No backend/fixture timeout or
  assertion was changed. A fresh full run and its actual result are recorded below;
  the earlier failure is not hidden by a configured CI retry.
- **P2, artifact privacy:** replaced broad automatic browser artifact upload with a
  content-free status artifact. Ordinary local Playwright failure-report settings remain.
- No confirmed new P0/P1 production regression; this is not proof of absence.

| Evidence level / check | Status | Observed result |
| --- | --- | --- |
| Branch/HEAD/clean baseline, remote inspection | PASS | Exact starting commit, correct origin; partial remote settings visibility as above |
| Windows existing runtime preflight | PASS | Python 3.12.16; actual sqlite3 and aiosqlite SQLite 3.53.1 |
| Initial backend full suite | FAIL | 1657 passed, one fixture timeout, one Starlette/httpx deprecation warning |
| Isolated backend failure case | PASS | One passed; same dependency warning |
| Final backend full suite | PASS | 1658 passed, zero failed/skipped; one known Starlette/httpx deprecation warning; no backend/fixture changes |
| Backend/scripts strict mypy | PASS | 140 source files; existing app/tests/migrations/benchmarks plus scripts |
| Ruff lint / formatting | PASS | Backend and helpers checked with explicit repository configuration |
| Initial frontend full suite / isolated reproduction | FAIL | 191 passed/one failed; isolated same history-row assertion failed |
| Corrected history file | PASS | 23 passed, no skipped cases; the repaired case also passed three independent focused runs (each intentionally deselected 22 others) |
| Final frontend full suite / typing / ESLint | PASS | 192 passed in 9 files, zero failed/skipped; TypeScript and ESLint passed |
| Build / full Chromium | PASS | Production build passed; 34 Chromium cases passed, zero failed/skipped, one worker, zero retries |
| Workflow YAML/structure, inline code and privacy probes | PASS | Existing PyYAML 6.0.3 BaseLoader; job/trigger/pin/security assertions; Git Bash parsed 20 run blocks; 2 inline Python blocks parsed; sensitive-extra/missing/malformed/pre-existing-file sanitizer probes and 17 hygiene probes passed |
| Documentation/hygiene/whitespace/cleanup/DB non-mutation | PASS | 213 local links in 49 Markdown files; filename and 10 operator-document checks; tracked/untracked and committed-comparison whitespace; exact post-test cleanup block; no inspected test listeners/processes; development DB hash/size/mtime unchanged, sidecars absent (private values not published) |
| Local Linux environment | BLOCKED | Only internal Docker Desktop distro; unsupported CLI there |
| Linux connected runtime, Linux suites, GitHub Actions | PENDING | No push/PR/remote execution in this phase |
| Live-provider tests / public deployment | NOT APPLICABLE | No paid calls; unsupported deployment boundary unchanged |

Windows checks used the existing qualified private Python 3.12.16 environment,
uv 0.13.6, Node 24.11.1 and npm 11.6.2. Installed application dependencies/Chromium
were reused; no fresh Linux dependency install, OS package/browser installation or
native action execution is claimed. The existing PyYAML parser runs under a separate
installed Python 3.10 solely for read-only syntax validation; it is not an application
interpreter or substitute for runtime qualification. Git Bash syntax parsing on
Windows is not Linux execution. actionlint was not available and was not installed;
local structural assertions do not replace GitHub's own workflow validation.

Warnings: the known Starlette/httpx TestClient deprecation occurs in backend runs;
Chromium emitted the existing NO_COLOR/FORCE_COLOR warning. The initial backend
failure also logged SQLAlchemy cancellation during fixture termination, as recorded
above. No application exception or test failure is discarded from this evidence.

## Files and Git boundary

- [Existing CI workflow](../.github/workflows/ci.yml): four jobs and the described controls.
- [Linux qualification helper](../scripts/verify_ci_linux_runtime.py): CI-only architecture/linkage/capability gate, reusing existing policy.
- [Repository hygiene helper](../scripts/check_ci_hygiene.py): read-only filename/operator-document checks.
- [History test](../frontend/src/test/PromptHistory.test.tsx): same assertion synchronized with the current request/DOM.
- [Root README](../README.md): CI report navigation; no speculative badge.
- [Release checklist](local-mvp-release-checklist.md): link to this separately dated phase.
- This CI report.

No production application, dependency/lockfile, migration/model, scoring/fencing,
listener/security policy or existing documentation checker change. The M5F-5B
privacy correction and historical audit evidence remain intact. Branch and HEAD
remain the starting values; this package is working-tree-only, not staged/committed.
Final scope is four modified tracked files and three new untracked files. No push,
PR, merge, tag or branch switch is performed. The one observed backend fixture timing
failure remains a P2 reliability observation even though the fresh full run passed;
its delay cause was not established and must not be hidden by CI retries.

## Later controlled remote verification

After review and explicit commit/push approval, M5F-5C-2 can publish the feature branch
and inspect its CI run. Do not treat local YAML parsing as server-side Actions validation.
Record the exact checked SHA, OS/architecture, Python path/version, both connected SQLite
versions, actual linkage/WAL probe, full passed/failed/skipped counts and cleanup result.
All four named jobs must pass on the relevant revision; rerunning an unchanged flaky
check is not a substitute for investigating it. Inspect safe job logs and the minimal
failure status; reproduce using synthetic data, not uploaded DB/trace contents.

If the Linux runtime gate fails, keep merge blocked and investigate the pinned artifact
and compatibility policy without bypassing it. If a suite fails, isolate the scenario
and distinguish runtime, fixture and production defects; apply only reviewed narrow fixes.
PR checks must later pass on the merge revision too. Branch-protection/ruleset policy
and final merge remain separately authorized M5F-5C-2/3 work; no release badge or
released/merged/remote-green claim is made here.

## M5F-5C-2 hosted evidence and M5F-5C-2A local correction

The feature branch was published at
`448dd5a6f5bf9f3f56a0d469ede94540877044a5`. The push-event
[run 42726363096](https://github.com/sinanturdu/PromptForge-AI/actions/runs/42726363096),
attempt 1 on 2026-11-10, completed with **FAILURE** on Ubuntu 24.04 x86_64.
This is actual hosted evidence, separate from the earlier Windows results.

| Hosted job | Result |
| --- | --- |
| Backend, job 129532892297 | FAIL in CI helper; Ruff, format, mypy and pytest skipped |
| Frontend, job 129532892728 | PASS: TypeScript, ESLint, 192 tests in 9 files and production build |
| Chromium, job 129532891934 | FAIL in CI helper after browser installation; browser suite and post-suite cleanup check skipped |
| Documentation, job 129532892382 | PASS: 213 links in 49 Markdown files, hygiene and committed-diff whitespace |

Both failing jobs observed Python **3.12.16**, actual sqlite3 and aiosqlite connected
SQLite **3.53.1**, DB-API threadsafety 3 and `THREADSAFE=1`. The existing version
policy passed. The CI-only helper then attempted `_sqlite3.__file__` and raised
`AttributeError`: that attribute is absent in the selected built-in module build.
Neither linkage inspection nor the disposable WAL/FULL/foreign-key probe completed.
No backend or Chromium tests executed; this run does not qualify the release.

This P2 CI-helper portability defect is a release qualification blocker. The
[pinned upstream build notes](https://github.com/astral-sh/python-build-standalone/blob/fa506e0858ac9f2cc5b289c137b888a2aa24d1ee/docs/technotes.rst)
describe extensions compiled into Python. Such a built-in has no separate extension
file to pass to ldd. Missing `__file__` alone is not sufficient evidence of this mode.

The local correction is confined to import-mode classification and linkage inspection:

- A built-in requires the `_sqlite3` module/spec name, `origin='built-in'`, the
  built-in importer, no file location and membership in `sys.builtin_module_names`.
  The diagnostic explicitly states there is no extension file and external library
  linkage was not inspected. It does not fabricate a path or claim ldd ran.
- A shared extension requires a real existing extension file, the extension loader,
  consistent resolved module/spec/loader paths, a valid extension suffix and no
  contradictory built-in membership. It retains ldd and fails on inspection errors.
- Unknown, missing or contradictory import metadata fails closed. All platform,
  override, Python/connected-version, compile/thread and real database checks remain.
  The probe still disposes its engine and removes its owned temporary directory.

The new offline regression tests simulate import metadata and ldd only. They run
the actual connected-version preflight and real disposable writer/probe for both
modes, check runtime/probe failure propagation, and verify disposal and cleanup.
These simulations do not establish that the corrected helper passes on real Linux.

Local correction verification (Windows, existing private qualified environment):

| Check | Status / evidence |
| --- | --- |
| Focused import-mode regression tests | PASS: 22 passed, zero failed/skipped |
| Full backend suite | PASS: 1680 passed, zero failed/skipped; 437.13 seconds |
| Strict mypy | PASS: 141 source files including migrations, benchmarks and scripts |
| Ruff lint / format | PASS: backend and scripts; repository configuration preserved |
| Actual Windows runtime preflight | PASS: Python 3.12.16; both real connected SQLite versions 3.53.1 |
| Documentation / hygiene / whitespace / development DB non-mutation | PASS: 213 links in 49 Markdown files; filenames and 10 operator documents; tracked/untracked whitespace; DB SHA-256, size and modification time unchanged, WAL/SHM absent (values private) |
| Frontend / browser reruns for this correction | NOT APPLICABLE: no frontend, browser or production changes; hosted frontend result above remains evidence for the published commit only |
| Corrected real Linux helper and full hosted regression | PENDING: correction remains local and uncommitted |

No workflow, production code, dependencies, migration, security policy or provider
configuration changes. No paid calls, workflow reruns, settings changes or PR creation.
Concrete local database fingerprints remain private. The release checklist's local
MVP boundary remains unchanged; no additional checklist edit is needed.
The full suite emitted one existing Starlette/httpx TestClient deprecation warning.
An initial sandbox-restricted focused invocation stalled at async test startup and
was stopped; the reported focused/full runs used the qualified interpreter outside
that restriction. No test timeout, retry, skip or compatibility setting was changed.

Next: independent full diff review, then separately approved commit/push to the
existing branch. Verify a new hosted run on that new SHA, including the real
capability probe and all four complete jobs, before Gate C PR approval. Do not
rerun the unchanged failed commit as evidence for this correction.

## M5F-5C-2A hosted verification and M5F-5C-2B local correction

[Hosted run 42730730256](https://github.com/sinanturdu/PromptForge-AI/actions/runs/42730730256)
completed on published commit `5ce9cdf2bcfbdd19900ffbd9c466a70ba072b06a`.
The Backend and Chromium jobs both passed the real Linux runtime gate with
Python 3.12.16, connected SQLite 3.53.1 through each driver, and the disposable
WAL/FULL/foreign-key probe. This verifies the built-in SQLite correction on Linux.

| Hosted check | Result |
| --- | --- |
| Linux SQLite qualification | PASS in Backend and Chromium |
| Frontend | PASS, 192 tests |
| Chromium | PASS, 34 tests and post-suite cleanup |
| Documentation | PASS, 213 links plus hygiene and whitespace |
| Backend Ruff lint and format | PASS |
| Backend strict mypy | FAIL, one `attr-defined` error in 141 source files |
| Backend pytest | NOT RUN, mypy stopped the job |

Linux mypy reported `Module has no attribute "SO_EXCLUSIVEADDRUSE" [attr-defined]`
at `backend/app/core/execution_environment.py:123`, inside the Windows branch
of listener preparation. The local correction retrieves
the attribute dynamically and requires an integer matching the Winsock definition
before applying the option. An absent or invalid option raises before bind. A
`setsockopt` error propagates through the existing socket context manager, which
closes the socket. The lookup runs only on Windows.
See the [Windows SDK definition](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/WinSock2.h#L385).

The new tests cover option application before bind, missing and invalid metadata,
propagation of socket option errors, and the non-Windows path. Existing tests
exercise IPv4 and IPv6 listener ownership, handoff and qualification.

Windows strict mypy passed across app, tests, migrations, benchmarks and scripts
(141 source files). The same Windows-hosted command with `--platform linux`
reproduced the hosted error using the committed file via `--shadow-file` and
passed all 141 files with the correction. A native Windows socket check
confirmed exclusive use and closure on both IPv4 and IPv6 loopback listeners.
The focused suite passed 217 tests, including 10 new cases. Full backend pytest
passed 1,690 tests in 392.73 seconds with zero failures or skips. Both runs
reported the existing Starlette/httpx TestClient deprecation warning. Ruff lint
and formatting passed. The Windows runtime preflight verified Python 3.12.16
and connected SQLite 3.53.1 through both drivers. Documentation, hygiene and
Git whitespace checks passed. The development database SHA-256, size and
modification time matched the private baseline, and WAL/SHM files were absent.

Linux mypy and hosted Backend pytest for this correction remain pending. After
independent diff review and a separately approved commit and push, verify all
four jobs on the new SHA before Gate C.
