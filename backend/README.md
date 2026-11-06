# PromptForge AI — Backend

FastAPI modular monolith with the Outcome-First Architect, independent Critic,
deterministic scoring, bounded optimizer and durable SQLite workflow history.
Ordinary PromptRequest APIs remain separate from explicitly enabled execution.

## Setup and startup

Follow [Windows local setup](../docs/local-mvp-setup.md) for frozen installation,
explicit interpreter selection, runtime qualification and a new dedicated database.
The [operations reference](../docs/local-mvp-operations.md) owns settings and modes.
Do not run migration commands against an unreviewed default target.

There is no automatic migration or schema creation at startup. Alembic head is
`0002_workflow_persistence`. Execution refuses the default `backend/data/promptforge.db`,
including aliases/hard links, and requires an existing migrated non-development file.

- Development/API-only: ordinary Uvicorn, with execution unavailable even if both flags are true.
- Authorized execution: `app.core.execution_startup` owns the actual verified loopback
  socket and supervisor. Use the [complete startup command](../docs/local-mvp-setup.md#start-the-same-origin-local-application).
- SPA: only that dedicated command supports `--frontend-dist`; Vite is not an execution proxy.

## HTTP surface

| Surface | Behavior |
| --- | --- |
| GET `/api/v1/health` | `status=ok`, service name; not DB/provider/execution readiness |
| POST `/api/v1/prompt-requests` | Persist one request; no workflow or provider call |
| GET `/api/v1/prompt-requests` | Server `q`/`target_mode` filtering and pagination |
| GET `/api/v1/prompt-requests/{id}` | Reopen saved request/preferences |
| Workflow GETs | Six guarded reads, registered only with API flag |
| Workflow POSTs | Separate create/start; both flags plus qualified live owner |
| `/docs`, `/redoc`, `/api/v1/openapi.json` | Registered contracts, not execution readiness |

[Workflow contracts](../docs/workflow-api-contracts.md) own fields, scopes, idempotency,
error codes and privacy projections. Saving, creating and starting are separate actions.
GETs/recovery never authorize replay, repair or lease reset. No authentication exists.

## Checks

From `backend/`, after [setup](../docs/local-mvp-setup.md) has qualified `.venv`:

```powershell
$backendPython = (Resolve-Path '.venv\Scripts\python.exe').Path
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

Tests enforce fake/offline operation and protect the development DB fingerprint.
WAL tests refuse an unsafe connected runtime; no DELETE fallback or bypass is supported.
`uv.lock` stays frozen. Activation is optional; direct paths avoid selecting another Python.
CI pins 3.12.16 and checks actual connected SQLite before backend/Chromium tests;
remote qualification remains a release gate.

## Architecture map

| Code / guide | Responsibility |
| --- | --- |
| `app/core/config.py`, `.env.example` | Settings and default-off flags |
| `app/main.py` | App factory, lifespan-owned provider/engines |
| [LLM foundation](app/llm/README.md) | Providers, OpenAI adapter, retry/repair/deadlines |
| [Agents](app/agents/README.md), [benchmarks](benchmarks/README.md) | Versioned Architect contracts and offline quality fixtures |
| [Critic](../docs/prompt-critic.md), [optimizer](../docs/prompt-optimizer.md) | Blind evaluation, scoring, bounded revisions/selection |
| [Persistence schema](../docs/workflow-persistence-schema.md) | Five tables and versioned JSON storage |
| [Write boundary](../docs/sqlite-write-transactions.md) | Connected runtime policy, WAL/FULL, short transactions |
| [Workflow services](../docs/workflow-persistence-services.md) | Creation, claim, fencing, operation identity |
| [Atomic evaluation](../docs/atomic-evaluation-persistence.md) | Evaluated versions, selection, finalization |
| [Persistent execution](../docs/persistent-optimizer-execution.md) | Existing optimizer-to-storage adapter |
| [Readback/recovery](../docs/workflow-readback-recovery.md) | Validated snapshots; diagnostic-only recovery |
| [Admission](../docs/execution-admission.md), [supervisor](../docs/execution-supervisor.md) | One owner, no queue, guarded dispatch and bounded shutdown |

Errors use sanitized RFC 9457 Problem Details and server-generated `X-Request-ID`.
SQL engines hide bound parameters; SQL literals and raw object dumps still require care.
Protect database/build files and backend-only keys. [Security and residual risks](../docs/local-mvp-operations.md#security-and-threat-model)
are local-MVP limits, not production certification.
