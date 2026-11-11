# Local MVP operations, configuration and security

Use [setup](local-mvp-setup.md) first. This is one trusted local operator/process,
not authenticated hosting. The [M5F-5A audit](m5f-security-regression-audit.md) is the
primary security evidence; [workflow contracts](workflow-api-contracts.md) define HTTP behavior.

## Operating modes

The table assumes requests meet the applicable peer/Host/Origin checks and storage
is valid. Ordinary PromptRequest create/read APIs and health remain registered in
every valid application configuration. **Workflow-read-only is not a read-only whole API.**

| Flags / startup | Workflow GETs | Workflow create/start | Built SPA / execution |
| --- | --- | --- | --- |
| API=false, execution=false; ordinary Uvicorn | Absent, 404 | Absent, 404 | No SPA; no execution |
| API=true, execution=false; ordinary Uvicorn | Six guarded GET routes | Create path: 405; execution path: 404; neither POST registered | No SPA; no writer/supervisor |
| API=false, execution=true | Settings validation fails | Application does not start | Invalid combination |
| Both=true; ordinary Uvicorn | Guarded GETs | Registered, but 503 `workflow_execution_environment_unsafe` without owned execution owner | No SPA; no execution |
| Both=true; dedicated startup, qualification fails | Not an operational execution service; startup exits | No qualified service | Fails closed; no permissive fallback |
| Both=true; qualified `app.core.execution_startup` | Guarded GETs | Explicit create (201/200 replay), separate start (202) | SPA only with explicit `--frontend-dist`; one supervisor |

If a path is otherwise absent, routing can produce 404 rather than 405. Neither route
registration, OpenAPI nor a successful GET proves a writer/slot/provider is ready.
The dedicated command rejects flags not both true. Provider configuration alone is
not authorization. Execution eligibility/capacity is checked again for each action.

Vite development rejects workflow proxy paths before forwarding (403), even if a
backend is qualified. Vite preview is static inspection. Only the dedicated command
can install the compiled SPA on the verified API listener. There is no API-only SPA mode.

## Configuration reference

Source of truth: [Settings](../backend/app/core/config.py), [example](../backend/.env.example),
[model contract](../backend/app/llm/schemas.py), [startup CLI](../backend/app/core/execution_startup.py).
Settings are case-insensitive; process variables override `backend/.env`. Unknown env-file
keys are ignored, so a misspelled variable can silently have no effect. Do not dump Settings
or a real env file to diagnostics. Default API prefix should remain `/api/v1` for this SPA.

| Variable | Default / accepted values | Required / mode / implications |
| --- | --- | --- |
| `APP_NAME` | `PromptForge AI`, string | Optional, all modes; health service/application title |
| `APP_VERSION` | `0.0.1`, string | Optional, all modes; metadata, not release approval |
| `API_V1_PREFIX` | `/api/v1`, string | Optional for backend; built SPA explicitly requires `/api/v1` |
| `LOG_LEVEL` | `INFO`; DEBUG/INFO/WARNING/ERROR/CRITICAL, normalized uppercase | Optional; protect diagnostic destinations, avoid raw private objects |
| `MAX_REQUEST_BODY_BYTES` | 262144; positive integer | Optional, all modes; HTTP body bound, not a concurrency/rate limit |
| `DATABASE_URL` | Absolute `sqlite+aiosqlite` URL to `backend/data/promptforge.db` | Explicit dedicated file for qualification; execution CLI overrides with `--database`; workflow read engine requires plain file SQLite |
| `DATABASE_CONNECT_TIMEOUT` | 30.0; positive seconds | Ordinary engine/migrations; not the dedicated workflow engine busy-timeout control |
| `DATABASE_ECHO` | false; boolean | Ordinary SQL logging; keep false. Bind hiding does not sanitize SQL literals/raw dumps |
| `WORKFLOW_API_ENABLED` | false; only `true`/`false` strings or internal bool | Enables six guarded workflow reads; no execution by itself |
| `WORKFLOW_EXECUTION_ENABLED` | false; same strict flag values | Requires API flag; only qualified dedicated startup can activate owner/writer |
| `LLM_PROVIDER` | `fake`; `fake` or `openai` | Optional, backend-only; saved target family is independent |
| `OPENAI_API_KEY` | None; nonblank backend secret for OpenAI | Required only for OpenAI composition; never put in a VITE variable, command history or tracked file |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 20; finite >0, <=300 | Gateway setting; supervised execution additionally refuses values >20 |
| `LLM_CALL_DEADLINE_SECONDS` | 90; finite >0, <=900 | Shared operation budget; supervised execution additionally refuses values >90 |
| `LLM_MAX_RETRIES` | 2; integer 0–2 | Shared operation-wide transport retries; can incur additional remote calls |
| `LLM_JSON_REPAIR_ATTEMPTS` | 1; integer 0–1 | Shared structured-output repair budget; not an evaluation round |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Frontend development only; cannot bypass workflow proxy denial |
| `VITE_API_BASE_URL` | Empty | Legacy PromptRequest transport; keep empty for local SPA; workflows always relative `/api/v1` |
| `PROMPTFORGE_E2E_PYTHON` | Absent; fixtures then use backend `.venv` | Test-only exact interpreter path; invalid override fails; no app-runtime effect |

There are **no** application `APP_HOST`, `APP_PORT`, `APP_ENV` or model-name env settings.
Do not infer them from conventional names. Listener/model choices use actual CLI options:

| CLI option | Default / requirement |
| --- | --- |
| `--database` | Required existing migrated non-development SQLite file; no default |
| `--architect-model`, `--critic-model` | Required model identifiers; no default; each uses ModelSettings defaults (temperature unset, max_output_tokens=2048) |
| `--frontend-dist` | Optional trusted compiled directory with `index.html` and `assets/` |
| `--host` | `127.0.0.1`; only `127.0.0.1` or `::1` |
| `--port` | 8000; helper validates 0–65535 (0 is for isolated tests) |

No reload/workers/proxy/FD/UDS/TLS/root-path options are offered by dedicated startup.
Settings accepting an async PostgreSQL URL is not workflow runtime/PostgreSQL qualification.

### Secrets and repository hygiene

Keep real keys only in the backend process environment or a protected ignored
`backend/.env`. The OS process environment is not a secret vault against a hostile
local administrator. Do not paste keys into commands, URLs, logs, screenshots or reports.
`OPENAI_API_KEY` is represented as SecretStr, but dumps/validation payloads and raw SDK
objects remain sensitive. The SDK/HTTP environment (for example proxy settings) is not
an application authorization mechanism; review it before a separately approved live call.

Existing `.gitignore` excludes env files except `.env.example`, keys/credentials,
backend data, DBs/sidecars, `.venv`, node_modules, builds, caches, logs, tmp and Playwright
outputs. Ignore rules do not remove files already tracked. Check `git status --short`
and tracked files before publishing; do not automatically add generated files.
No ignore-rule change is needed for this package.

## Providers and cost safety

- **Fake testing:** no credentials/network generation; scripted fixtures drive deterministic
  preserve/rewrite/clarify, three rounds, failure and result states. The stock CLI fake
  has an empty script and fails generation safely; it is not an AI demo.
- **Configured real provider:** set `LLM_PROVIDER=openai` and supply the backend-only
  key through a protected channel. Both models must be chosen explicitly after checking
  account access and structured-output compatibility. There is no automatic model selection.
- **Explicit optimization:** saving or creating does not generate. Only the separate start
  action in a qualified runtime dispatches the existing Architect/Critic workflow.

To validate construction without requesting generation, from `backend/` with the exact
qualified `$backendPython` and your deliberately selected process settings:

```powershell
& $backendPython -c 'import asyncio; from app.core.config import Settings; from app.llm.composition import create_provider; p = create_provider(Settings()); asyncio.run(p.aclose()); print("Provider constructed and closed; no generation requested.")'
if ($LASTEXITCODE -ne 0) { throw 'Provider configuration failed.' }
```

This checks local Settings/adapter construction, not credential validity, remote model
access, costs or semantic quality. M5F-5B executes only its fake-provider form.

A future real-provider smoke check requires explicit approval of model IDs, synthetic
input, dedicated DB and possible cost before starting. Configure the key privately,
replace the setup command's fake provider/models deliberately, then create and start
one new workflow explicitly. Inspect persisted status/result and provider-side usage.
This procedure is **not executed or qualified** here. Never repeat an unknown start
automatically, rotate its key to force a replay or infer cancellation from a browser error.

The gateway owns retries: structured output can use one initial request, one JSON repair
and up to two shared transport retries (four actual attempts). Timeout/connection failure
may mean the upstream already ran and billed. Usage may be incomplete; persisted fences,
idempotent creation and one AgentRun do not guarantee exactly-once remote execution/billing.
Critic scores and JSON validity are not independent real-world quality guarantees.

## Authoritative execution limits

Verified in [admission](../backend/app/api/execution_admission.py),
[supervisor](../backend/app/core/execution_supervisor.py), gateway policy and optimizer contracts:

| Resource | Bound / meaning |
| --- | --- |
| Concurrent workflows / waiting queue | 1 / 0 per local owner |
| Concurrent local provider dispatch | 1; no promise about remote work surviving timeout |
| Evaluation rounds / agent operations | Up to 3 / 6 (Architect + Critic per completed round) |
| Gateway attempts per operation / workflow | Up to 4 / 24, not a guaranteed count or billing ledger |
| Outer execution deadline | 300 seconds; persisted config may set a smaller bound |
| Confirmed lease / safety margin | 120 / 15 seconds; renewal only between operations |
| Graceful drain / cancellation cleanup | 30 / 10 seconds; uncooperative work can still prevent exit |
| Supervised request timeout / operation deadline | At most 20 / 90 seconds |
| Workflow read/write busy timeout | 1000 ms on their dedicated engines; no automatic busy retry |
| Frontend GET / active polling | 10-second deadline / about 2 seconds after preceding read, visible healthy active monitor only |

| Event | Supported behavior |
| --- | --- |
| Capacity full | 429 with safe Retry-After; no queued work; any retry is an explicit user action |
| Provider timeout | Gateway may use its bounded retry budget; remote outcome/billing may be unknown |
| Browser disconnect | Owned backend task remains; browser cancellation is not remote cancellation |
| Shutdown | Close admission/new dispatch, drain, then cancel/clean up within bounds; retain resources if task is stuck |
| Lost lease/fence/environment | Stop new dispatch; cancellation/uncertainty does not invent terminal success/failure |
| Unresolved external operation | Preserve evidence and inspect readback/recovery; no automatic replay/takeover |
| Failed persistence acknowledgment | At most bounded persistence-only reconciliation; never repeat the agent dispatch to repair a receipt |
| Integrity failure | Withhold trusted selected content/proofs; stored records remain available for investigation |

These are cooperative in-process bounds. A blocked event loop cannot execute a watchdog.
Do not promise hard-real-time shutdown, power-loss durability proof or upstream cancellation.

## Database maintenance

1. Use a new dedicated file for setup qualification; never the existing development DB.
   Migrations run explicitly with the chosen `DATABASE_URL`. Execution's `--database`
   overrides Settings and refuses the default/aliased development file.
2. Migration head is `0002_workflow_persistence`; startup checks existing schema but does
   not migrate, provision, repair or reset it. A missing table/version requires investigation,
   a backup and an explicit reviewed migration, not a blind repeated command.
3. Stop all processes using the database and wait for shutdown before maintenance.
   Do not forcibly remove leases/rows to clear capacity. Recovery is read-only.
4. A live SQLite file copy can omit committed WAL data. Never copy only the main file
   while active, and never delete WAL/SHM to make errors disappear. Preserve files for investigation.
5. For a separately approved backup, stop all users first, choose a **new destination**,
   and use SQLite's connection backup API or another reviewed SQLite-consistent procedure.
   Verify the backup with read-only `PRAGMA integrity_check`, `foreign_key_check` and
   Alembic revision inspection. A raw file copy is appropriate only after confirming
   a clean closed/checkpointed state. This package performs no backup/restore of personal data.
6. Restore into a separate new path with all users stopped; verify before any explicit
   startup/migration decision. Never overwrite the original as an automatic troubleshooting step.

Workflow writers use connected-version-gated WAL/FULL, foreign keys and short explicit
BEGIN IMMEDIATE transactions. Readback uses separate read-only snapshots. Ordinary
PromptRequest/migration engines retain their existing WAL/NORMAL policy; do not confuse
them with the workflow writer or silently change journal mode to bypass qualification.
SQL integrity checks do not establish workflow graph integrity; use validated readback too.

## Security and threat model

| Asset / boundary | Protection and assumption |
| --- | --- |
| Prompts, context, canonical/rendered text and Critic evidence | Sensitive local DB/content; metadata excludes bodies, explicit content GETs, plain-text UI, no browser persistence |
| Backend credentials | Backend-only SecretStr/configuration; no VITE variables, content logs or public DTOs |
| Operation receipts, tokens, leases and fences | Server-owned short transactions and fresh exact-fence checks before every actual provider attempt; never public authority from a GET |
| HTTP caller | Actual loopback peer, single allowed literal Host (`127.0.0.1`, `localhost`, `[::1]`), matching Origin when present; forwarded headers/cross-site fetches rejected |
| Execution authority | Actual exclusive owned Uvicorn loopback listener, one process/worker, both flags, safe runtime and dedicated existing DB |
| SPA/build | Trusted compiled directory; explicit browser routes, contained allowlisted assets; API/docs never become HTML fallback |
| Cached result | Parent/workflow-scoped in-memory revocation survives remount/switching; only new explicit validated result GET clears it, cancellation counter defense retained |
| Diagnostics | Fixed public errors, request IDs, content-free gateway metadata, SQLAlchemy parameter hiding; raw objects/SQL literals still unsafe |

Host/Origin/listener checks are not authentication. Direct trusted-local CLI requests
can omit browser headers after peer/Host checks. A hostile local user/admin, compromised
build/server/provider or header-stripping local proxy remains outside this boundary.
Use one trusted local account/process and protected data/build directories.

Result recovery closes/evicts previously inconsistent content history. Pending/canceled/
failed/timeout reads, progress/metadata and another workflow's success cannot restore
approval. Reload drops both private cached approvals and in-memory guards and queries
the backend. The guard is not cross-tab/disk state; session submission markers are only
duplicate-action safeguards. Recovery diagnostics never authorize writes/replay.

Known limits: no authentication, public deployment qualification, cross-tab coordination
guarantee, HTTP flood protection, distributed queue, durable task delivery, automatic
recovery, remote cancellation or exactly-once billing. Linux/remote CI and cost-bearing
real-provider qualification remain unverified. Do not bind publicly or add proxies,
tunnels, multiple workers/listeners, reload, inherited sockets, root paths or TLS to
the qualified command. They require a separately reviewed architecture.
