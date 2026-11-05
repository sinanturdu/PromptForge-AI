# SQLite write transaction foundation — M5E-3A

## Implemented boundary and ownership

`backend/app/db/write_transactions.py` provides an opt-in `create_write_engine(url)`
and `write_transaction(engine)` context. Neither is wired into FastAPI, the optimizer,
repositories, a runner or Alembic. No workflow executes and no schema changes here.
The existing `DatabaseSessionManager`, PromptRequest services and API responses are
unchanged. This boundary does not load configuration or default to a database URL.
The legacy PromptRequestService owns its API commit and must not be called inside
this new unit; future services reuse repository flushes without that legacy commit.

Future service composition owns the dedicated engine and eventually disposes it.
Each unit owns a fresh AsyncSession and exactly one short root transaction:

1. Open a connection and validate/configure it before any transaction starts.
2. Explicitly begin and acquire the SQLite writer reservation **before yielding**.
3. Repositories execute/flush multiple writes; they never commit or roll back.
4. Normal context exit commits; errors, failed commits and cancellation roll back.
5. An outer session context always closes, even when transaction exit itself fails.
   Sessions are permanently closed and cannot accidentally be reused afterward.
6. Only after exit may the service await a provider/network operation. Re-enter a
   fresh unit to recheck ownership/state and persist results after that operation.

```python
# Future service composition only; not activated by this milestone.
engine = create_write_engine(explicit_workflow_database_url)
try:
    async with write_transaction(engine) as session:
        # Load/check/reserve and flush bounded database work here.
        ...
    # No session/transaction remains open across a model/network call.
finally:
    await engine.dispose()
```

The context cannot inspect arbitrary awaits in caller code: keeping network work
outside is a service integration rule. Do not manually commit inside it. Root
transactions must not be nested; `session.begin_nested()` deliberately creates a
SAVEPOINT within the existing root. A separate nested writer unit is a competing
connection, not a SAVEPOINT, and can time out against its own caller's lock.

`autobegin=False` prevents accidental implicit session transactions. `NullPool`
ensures physical connections close after each unit rather than retaining locks or
connection-local state. SQL parameter hiding is enabled. Do not change this engine's
isolation execution options, use it for migrations, or give it to read-only API routes.

## Explicit BEGIN and connection policy

SQLite uses SQLAlchemy's supported `isolation_level=None` connect event plus a
`begin` event emitting `BEGIN IMMEDIATE`. This replaces the driver's implicit BEGIN
behavior; it is not mixed with `autocommit=False` or SQLAlchemy AUTOCOMMIT. Real
connection tests verify a physical transaction exists before the first read, DDL is
transactional, and released SAVEPOINT changes still roll back with the root.
See [SQLAlchemy's sqlite3/aiosqlite transaction recipe](https://docs.sqlalchemy.org/en/21/dialects/sqlite.html#enabling-non-legacy-sqlite-transactional-modes-with-the-sqlite3-or-aiosqlite-driver).

The write-only engine configures and verifies journal mode, `synchronous=FULL` and
`foreign_keys=ON` on a new connection, outside an active transaction. The busy timeout
defaults to 1,000 ms and accepts integers 1–30,000 ms. Changing synchronous inside
BEGIN is illegal; changing FK enforcement there is ineffective. Tests cover both.
The database's parent directory must exist; engine creation itself is lazy.

WAL is the requested durable workflow mode and must pass the runtime gate first.
DELETE is explicitly available for isolated transaction tests; it is never an
automatic fallback or approval to activate workflows. Journal mode affects the file;
do not point this factory at the development DB without separate provisioning approval.
No development DB connection, migration or PRAGMA change was performed in M5E-3A.

SQLite has one writer, not row-level SELECT FOR UPDATE. Reserving it before reading
avoids upgrading a stale read snapshot (`SQLITE_BUSY_SNAPSHOT`). Other writers still
receive `SQLITE_BUSY` after the bounded timeout. In DELETE mode a reader can also
block a writer's COMMIT. The unit rolls back these failed attempts without partial
writes. There are no automatic retries, arbitrary delays or replay. A later service
must handle admission/conditional state changes and idempotency explicitly.

Cancellation cannot instantly interrupt a SQLite busy handler running in the driver
thread; cleanup can wait for its bounded timeout. Cancellation is propagated after
cleanup in the tested paths. Cancellation near a successful COMMIT can leave outcome
acknowledgment uncertain; future services must reconcile operation identity, not
assume every cancelled caller means no commit. These tests do not simulate power loss.
FULL provides the chosen SQLite WAL commit-durability policy, subject to filesystem/
hardware guarantees; NORMAL has different power-loss guarantees.

## Runtime capability gate

`sqlite_runtime.py` classifies the result of **SELECT sqlite_version() on the actual
aiosqlite connection**, not a CLI or Python package's version attribute. Known fixed
versions are 3.51.3 and later SQLite 3 releases, plus documented backports 3.44.6 and
3.50.7. Other releases in the affected 3.7.0–3.51.2 range are refused; unknown formats,
custom labels and major versions fail closed. Vendor fixes with unchanged version
strings require explicit policy review. This is a prerequisite, not authorization.
[SQLite's WAL-reset defect and fixed versions](https://www.sqlite.org/wal.html#walresetbug).

An unsafe/unverified runtime raises a fixed diagnostic before changing journal mode
or yielding a writer session. It does not include the URL, credentials or prompt
content. Normal application startup, health checks and legacy APIs do not invoke
this gate. Passing it alone does not enable a workflow.

## Verification environment and safe test modes

| Category | Actual connected runtime / settings |
| --- | --- |
| Backend sqlite3 and aiosqlite | Python 3.12.7, SQLite 3.45.3; aiosqlite 0.22.1, SQLAlchemy 2.1.6. |
| New atomicity, contention, cancellation and SAVEPOINT tests | SQLite 3.45.3; disposable files, DELETE / FULL / FK ON. Independent connections and driver trace events synchronize contention. |
| WAL admission on the actual backend runtime | SQLite 3.45.3 is refused before enabling WAL. No affected-runtime concurrent WAL test. |
| Legacy regression tests | Same backend runtime; existing sequential WAL/NORMAL behavior and migrated temporary DBs are retained. |
| Version-classification tests | Synthetic fixed/affected/unknown strings only; no claims about executing those libraries. |
| Other installed interpreters, in-memory connection probes only | Python 3.13.9 and 3.13.14 both report SQLite 3.50.4 (affected). |

Tests additionally cover repository flushes remaining uncommitted, deferred-FK and
reader-blocked COMMIT failures, permanent session close, physical connection close,
nontransactional PRAGMA application, lazy creation and refused WAL initialization.
Bounded test timeouts guard deadlocks; synchronization uses events, not sleeps.
The full API/optimizer/migration suite and development DB hash/mtime/size/sidecar
guards remain in place. No real provider calls or alternative SQLite drivers run.

## Patched runtime recommendation — approval required separately

No installed runtime verified here can activate concurrent WAL workflows. No Python,
package, lockfile, PATH or registry change has been made. A newer SQLite CLI does
not replace the SQLite library linked into Python.

The smallest compatibility candidate is an **isolated uv-managed Python 3.12.16**,
keeping this project's `>=3.12,<3.13` requirement. uv uses Astral's
[python-build-standalone](https://docs.astral.sh/uv/guides/install-python/). Its inspected
[build dependency manifest](https://github.com/astral-sh/python-build-standalone/blob/main/pythonbuild/downloads.json)
specifies SQLite 3.53.1, and the
[Windows build](https://github.com/astral-sh/python-build-standalone/blob/main/cpython-windows/build.py)
uses that dependency. This verifies the source build approach, **not the library in
an uninstalled release artifact**. Qualify the selected artifact using real connections
before choosing it for development/CI. The installed uv 0.13.6 catalog lists
`cpython-3.12.16-windows-x86_64-none` as available for download. A catalog entry or
source manifest alone is not proof of the installed artifact's SQLite safety.

These proposed PowerShell commands have **not been executed**. After approval, run
from `backend/`; installation is private and avoids executable/registry registration:

```powershell
$taskRuntimeRoot = Join-Path $env:LOCALAPPDATA 'PromptForge\sqlite-qualification'
uv python install 3.12.16 --install-dir $taskRuntimeRoot --no-bin --no-registry
if ($LASTEXITCODE -ne 0) { throw 'Runtime installation failed' }
$taskPython = Join-Path $taskRuntimeRoot 'cpython-3.12.16-windows-x86_64-none\python.exe'
& $taskPython -c 'import sqlite3; from app.db.sqlite_runtime import require_safe_sqlite_wal; c=sqlite3.connect(":memory:"); v=c.execute("SELECT sqlite_version()").fetchone()[0]; c.close(); print(v); require_safe_sqlite_wal(v)'
if ($LASTEXITCODE -ne 0) { throw 'Runtime capability verification failed' }
```

If that passes, separately approve dependency installation into a private qualification
environment (the project's current `.venv` and lockfile remain untouched):

```powershell
$previousProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
try {
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $taskRuntimeRoot 'backend-venv'
    uv sync --frozen --python $taskPython
    if ($LASTEXITCODE -ne 0) { throw 'Private environment setup failed' }
    @'
import asyncio
import aiosqlite
from app.db.sqlite_runtime import require_safe_sqlite_wal

async def probe():
    async with aiosqlite.connect(":memory:") as connection:
        async with connection.execute("SELECT sqlite_version()") as cursor:
            row = await cursor.fetchone()
        print(row[0])
        require_safe_sqlite_wal(row[0])

asyncio.run(probe())
'@ | uv run --frozen --python $taskPython python -
    if ($LASTEXITCODE -ne 0) { throw 'Async SQLite capability verification failed' }
    uv run --frozen --python $taskPython pytest
    if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed' }
    uv run --frozen --python $taskPython mypy app tests migrations benchmarks
    if ($LASTEXITCODE -ne 0) { throw 'Type checks failed' }
    uv run --frozen --python $taskPython ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Lint checks failed' }
    uv run --frozen --python $taskPython ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Formatting checks failed' }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousProjectEnvironment
}
```

Stop on any command failure. Keep WAL workflow activation disabled unless both
connection probes, full regressions and patched-runtime WAL qualification succeed.
The actual-WAL test automatically validates WAL/FULL/FK on fixed builds, without
pretending this current run exercised them. Pin the approved runtime/build in local
and CI configuration in a separately reviewed package before M5E-3B activation.

Alternatives considered:

- Official CPython 3.15.2 Windows build references patched SQLite 3.53.4 in its
  [release-tagged build definition](https://github.com/python/cpython/blob/v3.15.2/PCbuild/python.props).
  It exceeds the project's Python constraint, so requires a separately approved
  compatibility migration; it is not a drop-in fix. Inspected 3.13.17/3.14.10 build
  definitions still reference 3.50.4; newer Python alone is not sufficient evidence.
- An isolated custom Python 3.12 build against patched SQLite preserves the minor
  version but introduces build/distribution ownership; none was built or verified.
- Separately packaged SQLite drivers require Windows wheel/support and async-stack
  verification. [aiosqlite 0.22.1 explicitly calls stdlib sqlite3.connect](https://github.com/omnilib/aiosqlite/blob/v0.22.1/aiosqlite/core.py),
  so installing another module alone is insufficient. No driver monkeypatch or DLL
  replacement is proposed for this milestone.

## Portability and remaining work

The unit uses ordinary SQLAlchemy transactions on PostgreSQL and installs no SQLite
events there. Its driver/server integration is not tested here. PostgreSQL claim
locking and concurrency semantics still need future service design; SQLite's global
writer reservation must not be treated as portable row locking.

M5E-3B/3C still own repositories, loaded-row ownership checks, conditional claim/state
updates, operation idempotency, optimizer hooks and durable workflow writes. ADR-005's
future runner/interrupted-run policy is unchanged. This package adds no execution,
recovery, endpoints, new tables or migration modifications.
