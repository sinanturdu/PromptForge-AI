# Windows local MVP setup

This guide starts from a fresh clone in PowerShell. Use a trusted local Windows
account, one application process and a new dedicated database. No authentication
exists. Public hosting, proxies, containers and tunnels are unsupported.
See [operations](local-mvp-operations.md) and [release evidence](local-mvp-release-checklist.md).

## Prerequisites

- Git, uv, Node.js and npm installed by your normal trusted installation process.
  Command syntax was checked with PowerShell 7.6.9; a separate PowerShell 5.1 run
  was not performed.
  The observed tools for this qualification are uv 0.13.6, Node 24.11.1 and npm 11.6.2.
  `frontend/package.json` declares Node >=22.12; CI selects Node 24. Other toolchains
  are not qualified merely by satisfying that minimum.
- **Python 3.12.16** for supervised execution and full workflow tests. The project
  manifest allows Python >=3.12,<3.13, but execution and CI preflight require this exact patch.
- Connected sqlite3 **and** aiosqlite must pass `require_safe_sqlite_wal()`:
  SQLite 3.51.3+ within its recognized 3.x version format, or fixed backports 3.44.6
  or 3.50.7. Unknown/unsafe versions fail closed. Windows qualification observed 3.53.1
  in both drivers; this is not a promise about every Python distribution or Linux build.
- Dependency/browser downloads need network access on an uncached machine. No setup
  command here calls an LLM. Do not download replacement SQLite DLLs or monkeypatch drivers.

## Clone and select the interpreter

Run from the directory in which you keep projects:

```powershell
git clone https://github.com/sinanturdu/PromptForge-AI.git
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
Set-Location PromptForge-AI
$repoRoot = (Get-Location).Path
git status --short
git rev-parse HEAD
```

Use the reviewed revision intended for your installation. Do not assume the default
branch already contains an unmerged feature branch. The M5F-5B qualification started
at `ae19cd2` on `feature/m5f-workflow-api`; it does not create or switch branches.

Choose **one** interpreter acquisition path. To ask uv for its managed distribution:

```powershell
uv python install --managed-python 3.12.16
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
$qualifiedPython = (uv python find --managed-python --no-python-downloads --system 3.12.16).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Exact managed Python not found.' }
```

That download path is the configured CI approach, not a locally reproduced fresh
download in M5F-5B. It does not guarantee a safe connected SQLite build. Alternatively,
use an already approved exact interpreter (including an approved private environment):

```powershell
$qualifiedPython = Read-Host 'Absolute path to an approved Python 3.12.16 executable'
if (-not (Test-Path -LiteralPath $qualifiedPython -PathType Leaf)) { throw 'Interpreter not found.' }
$qualifiedPython = (Resolve-Path -LiteralPath $qualifiedPython).Path
```

The private Windows runtime used for qualification is not distributed by this repository.
If your distribution cannot pass the check below, stop: obtaining an independently
qualified build is a setup prerequisite, not permission to bypass the gate. A fresh
OS/toolchain installation was not reproduced during this package.

## Install and qualify the backend

From the repository root, in the same PowerShell session:

```powershell
Set-Location (Join-Path $repoRoot 'backend')
uv sync --frozen --no-python-downloads --python $qualifiedPython
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
$backendPython = (Resolve-Path '.venv\Scripts\python.exe').Path
& $backendPython -m app.db.verify_ci_runtime
if ($LASTEXITCODE -ne 0) { throw 'Stop: Python/SQLite qualification failed.' }
```

`uv sync` creates `.venv` and includes the development group from the frozen lockfile.
The check uses real in-memory database connections, prints all observed versions and
opens no application database. No fallback interpreter is permitted.

Activation is optional: `. .\.venv\Scripts\Activate.ps1`. If local PowerShell policy
blocks activation, continue using `$backendPython`; do not weaken machine policy.
All commands below invoke that exact executable, rather than relying on `python` or
an implicit `uv run` selection. In a new shell, resolve it again and rerun preflight.

## Configure safely and create a new database

Fresh clones need no real `.env` file. `backend/.env.example` documents defaults;
Settings reads `backend/.env` independent of working directory, with process variables
taking precedence. Never overwrite an existing `.env` or print its secrets. Review
inherited settings locally; the qualification path explicitly chooses fake mode.

Still in `backend/`:

```powershell
$env:LLM_PROVIDER = 'fake'
$env:WORKFLOW_API_ENABLED = 'false'
$env:WORKFLOW_EXECUTION_ENABLED = 'false'
$env:LOG_LEVEL = 'INFO'
$env:DATABASE_ECHO = 'false'
$runDirectory = Join-Path ([IO.Path]::GetTempPath()) ('promptforge-local-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runDirectory -ErrorAction Stop | Out-Null
$dbPath = Join-Path $runDirectory 'local-mvp.sqlite3'
if (Test-Path -LiteralPath $dbPath) { throw 'Refusing an existing database.' }
$env:DATABASE_URL = & $backendPython -c 'from pathlib import Path; import sys; from app.core.config import build_sqlite_url; print(build_sqlite_url(Path(sys.argv[1])))' $dbPath
if ($LASTEXITCODE -ne 0) { throw 'Database URL preparation failed.' }
& $backendPython -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
& $backendPython -m alembic current
if ($LASTEXITCODE -ne 0) { throw 'Migration status failed.' }
```

Expect `0002_workflow_persistence (head)`. This database is **disposable** and outside
`backend/data/`. Keep `$dbPath` for startup; do not substitute the development DB.
For real retained local data, deliberately choose a new protected persistent directory
instead of the temporary directory, preserve backups and follow [maintenance](local-mvp-operations.md#database-maintenance).
Startup never migrates or resets records. Existing data requires separate review before migration.

## Install and build the frontend

The local SPA must use an empty legacy API base. On a fresh clone there are no local
Vite env files. On an existing checkout, review `.env`, `.env.local` and mode-specific
files in `frontend/`; a production-mode file can override an empty/default base. Never
put credentials in `VITE_*`. Backend provider selection is unrelated to these variables.

```powershell
Set-Location (Join-Path $repoRoot 'frontend')
npm ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
$frontendDist = (Resolve-Path 'dist').Path
```

The trusted build directory must contain `index.html` and `assets/`. Do not point SPA
serving at source, uploads or a directory containing secrets. `dist/` is a compiled
frontend artifact, not a claim of production deployment readiness.

## Start the same-origin local application

Use the same PowerShell session/variables, after migrations and build succeed:

```powershell
Set-Location (Join-Path $repoRoot 'backend')
$env:WORKFLOW_API_ENABLED = 'true'
$env:WORKFLOW_EXECUTION_ENABLED = 'true'
$env:LLM_PROVIDER = 'fake'
$architectModel = 'fake'
$criticModel = 'fake'
& $backendPython -m app.core.execution_startup --database $dbPath --architect-model $architectModel --critic-model $criticModel --frontend-dist $frontendDist --host 127.0.0.1 --port 8000
```

Leave this foreground server running. Open `http://127.0.0.1:8000/` using exactly the
same scheme, host spelling and port for the browser and API. `/history` and saved
`/prompt-requests/{id}` routes support refresh. Relative `/api/v1` stays on this origin.
API/OpenAPI/docs paths never fall back to HTML; unknown paths remain errors.

The command owns the single actual loopback listener, qualifies Python and both SQLite
drivers, validates the existing DB schema and then activates one supervisor. It refuses
the default development DB, missing files, unsafe runtime or unsupported configuration.
No provider generation happens on startup, health, save or workflow creation.

**Fake-mode expectation:** save/read UI works, but the stock provider has no scripted
responses. An explicit start will end in a safe FAILED outcome (`script_exhausted`),
not a useful generated result. Successful deterministic optimization is demonstrated
by the scripted fake E2E fixtures below. Real-provider use is a separate explicitly
authorized, cost-bearing [procedure](local-mvp-operations.md#providers-and-cost-safety).

In another terminal, a non-generating check is:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health'
```

Expect `status=ok`, `service=PromptForge AI`. This checks HTTP availability, not provider
access, DB integrity or execution readiness. Save a synthetic prompt, reopen it from
history and verify preferences. Manual checks remain unverified until you perform them.

Stop with Ctrl+C. Admission closes before bounded draining/cancellation; wait for process
exit before database maintenance. A stuck cleanup is a failure requiring investigation,
not proof the remote call stopped. Do not force-reset leases or replay unknown operations.

## Development and API-only alternatives

Stop the dedicated server before using the same port. In `backend/`, retaining the
explicit dedicated `$env:DATABASE_URL` from above:

```powershell
$env:WORKFLOW_EXECUTION_ENABLED = 'false'
$env:WORKFLOW_API_ENABLED = 'false'
& $backendPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --reload
```

This is development only; ordinary Uvicorn has no execution owner. To inspect workflow
GETs, set `WORKFLOW_API_ENABLED=true` before starting ordinary Uvicorn and keep execution
false. That makes workflow reads available, **not the whole API read-only**: ordinary
PromptRequest POST remains available. No compiled SPA is installed in either mode.

In a second terminal at repository root: `npm --prefix frontend run dev`. Vite supports
ordinary prompt pages but refuses workflow proxy paths. `npm run preview` is only
static inspection. Do not remove the proxy guard or add CORS to activate execution.

## Automated checks

Stop interactive servers first. In `backend/`, after defining the exact `$backendPython`:

```powershell
& $backendPython -m app.db.verify_ci_runtime
if ($LASTEXITCODE -ne 0) { throw 'Runtime qualification failed.' }
& $backendPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'pytest failed.' }
& $backendPython -m mypy app tests migrations benchmarks
if ($LASTEXITCODE -ne 0) { throw 'mypy failed.' }
& $backendPython -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff lint failed.' }
& $backendPython -m ruff format --check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff formatting failed.' }
```

In `frontend/` (keep `$backendPython` defined as an absolute path):

```powershell
npm run typecheck
if ($LASTEXITCODE -ne 0) { throw 'TypeScript failed.' }
npm run lint
if ($LASTEXITCODE -ne 0) { throw 'ESLint failed.' }
npm run test
if ($LASTEXITCODE -ne 0) { throw 'Vitest failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
npx playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }
$env:PROMPTFORGE_E2E_PYTHON = $backendPython
npm run test:e2e
if ($LASTEXITCODE -ne 0) { throw 'Chromium tests failed.' }
```

Browser installation is needed only when absent; it may download binaries. Tests use
fake providers and fresh migrated databases, not `$dbPath` or `backend/data/promptforge.db`.
The E2E script builds first, uses one worker and zero retries, and cleans up owned
processes/databases. Linux CI also installs browser system dependencies and independently
qualifies SQLite; that configured path has not been qualified by this Windows run.

From root: `git diff --check`. Documentation-link checking is described in the
[release checklist](local-mvp-release-checklist.md). For startup errors, use the
[troubleshooting table](local-mvp-troubleshooting.md); never disable the safety gates.
