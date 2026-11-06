# Frontend workflow foundation — M5F-4A

## Existing application and scope

React 19 / Vite 8 / strict TypeScript use BrowserRouter (`/`, `/history`,
`/prompt-requests/:id`), the existing AppLayout/navigation, Tailwind/custom UI components,
and one AppProviders-owned TanStack Query client. There is no installed shadcn component
library to activate. NewPromptPage, PromptHistoryPage and PromptRequestDetailPage keep
their existing save/read behavior. No optimization button, progress/result screen,
automatic workflow creation or provider invocation is connected to those pages.

`src/features/workflows/` follows the existing feature layout. It borrows the existing
PromptRequest record type and QueryClient; it does not add a router, provider framework,
dependency, storage adapter or another QueryClient. The older shared API helper retains
its legacy configurable base URL and existing page behavior. Workflow transport is
feature-local because it must enforce fixed relative URLs and checked wire contracts,
which that helper does not currently guarantee.

## One verified origin

The dedicated `python -m app.core.execution_startup` command accepts the explicit,
optional `--frontend-dist` directory. It installs the trusted **built** SPA on the same
application and exclusive 127.0.0.1/::1 socket as the existing API. No Vite proxy or
second backend is involved. Without that option, ordinary startup and default-off
flags behave as before; ordinary Uvicorn cannot activate workflow execution or this SPA.

Both existing flags are still required by dedicated startup. Existing connected
Python/SQLite qualification, non-development database refusal, socket ownership,
one-process/one-worker restrictions, fencing, supervisor budgets and shutdown stay intact.
Every static request rechecks the live prepared listener and reuses the unchanged
peer/Host/Origin/fetch-site/forwarding policy (the shared predicate now has a public name).
Headers and ASGI addresses cannot substitute for listener proof. No CORS is added.

Static installation requires index.html, assets/ and the default /api/v1 prefix used
by this frontend. Only `/`, `/history`, `/prompt-requests/{id}`, `/favicon.svg` and
`/assets/{path}` are served. Known browser navigation paths get index.html; there is
no blanket SPA catch-all. Unknown paths, API/OpenAPI/docs errors and unsupported
methods keep their real responses. Assets are contained by resolved paths and a
build-file extension allowlist; dotfiles, source maps and arbitrary files are denied.
Use a trusted immutable build directory, never source, uploads, secrets or another
application's files. Rebuild/restart to deploy local changes.

HTML uses no-store; assets use no-cache/revalidation. These route-local headers cannot
override workflow API no-store/security headers. No service worker is introduced.

## Build and run locally

Use already installed dependencies and the qualified private environment. First choose
an **existing migrated disposable/non-development database**; provisioning/migration
remains a separate explicit operator action. Never use backend/data/promptforge.db.
From the repository root in PowerShell:

```powershell
$env:VITE_API_BASE_URL = ''
npm --prefix frontend run build
$taskPython = 'C:\Users\sinan\AppData\Local\PromptForge\sqlite-qualification\backend-venv\Scripts\python.exe'
$env:WORKFLOW_API_ENABLED = 'true'
$env:WORKFLOW_EXECUTION_ENABLED = 'true'
$env:LLM_PROVIDER = 'fake'
Push-Location backend
try {
  & $taskPython -m app.core.execution_startup --database 'C:\path\to\existing-disposable.db' --architect-model fake --critic-model fake --frontend-dist '..\frontend\dist' --host 127.0.0.1 --port 8000
} finally { Pop-Location }
```

Open http://127.0.0.1:8000 (use the same host spelling, scheme and port for browser/API).
Ctrl+C uses the existing admission close → bounded drain/cancellation → resource
cleanup lifecycle. No workflow starts simply because the page is opened. The stock
fake provider has no scripted replies; an explicit API start safely fails with
script_exhausted. The integration tests inject scripted fake replies and demonstrate
successful execution without charges. No real provider/credential is required.

`npm run dev` still supports the existing PromptRequest pages and mocks. Its pre-proxy
guard refuses all workflow API paths with 403; it never forwards workflow POSTs to a
loopback backend. Neither changing VITE_API_PROXY_TARGET nor setting VITE_API_BASE_URL
authorizes workflow execution. VITE_API_BASE_URL is ignored by the workflow client;
any legacy cross-origin setting applies only to non-workflow APIs. Vite preview is
static inspection, not the supported execution origin. Do not put keys in VITE_ vars.

## Contracts, errors and data handling

contracts.ts mirrors workflow_api_schemas.py and its nested serialization contracts.
Readonly TypeScript types derive from local checked parsers: required keys remain
required, nullable scores/telemetry stay null, decimal scores stay strings, and arrays
represent Pydantic tuples. Unknown/missing keys and wrong shapes are malformed responses.
There are separate metadata and explicit content types/functions; no include-content
default, raw provider result, execution token or hidden model identity is exposed.

The client provides create, start, progress, listing/keyset cursor, metadata history,
content history and terminal result functions. UUID path segments are validated;
queries are URL-encoded. Requests use relative /api/v1 URLs, same-origin mode and
credentials, no-store and redirect rejection. Every operation accepts AbortSignal.
Creation retains the caller's exact required JSON-body idempotency key; it generates
no replacement key and handles 201 creation/200 replay. Start accepts only 202 and
never retries. Its status is a pre-admission snapshot, not durable claim/completion.

WorkflowClientError distinguishes API, network, cancellation, malformed response,
invalid local request and duplicate in-flight start. HTTP 403/404/409/422/429/503/500
have fixed local messages. RFC 9457 bodies contribute only allowlisted error codes
and validated request IDs; raw body/detail/SQL/provider exceptions and causes are never
attached to errors or logged. Missing/malformed error bodies retain a safe HTTP category;
missing/malformed successful JSON is rejected. Client cancellation cannot undo an
already accepted server task: poll persisted state rather than automatically resubmit.

COMPLETED, FAILED and CLARIFICATION_REQUIRED remain execution states. Quality independently
uses qualified/not_qualified/unassessable. trustedSelection() withholds selections when
history fails integrity, is truncated or has findings. Result retrieval rejects active
or inconsistent graphs; it does not compute scores, invent telemetry or turn a failed
workflow with a retained candidate into a successful workflow.

## TanStack Query foundation

queries.ts supplies reusable query/mutation options, not mounted hooks/screens. Keys
separate parent/cursor pages, progress, metadata, sensitive content and result scopes.
Read functions consume the supplied cancellation signal. Inactive data has gcTime=0;
there is no persisted cache or console content logging. Default polling/retries/focus
refetch are off. A pure interval helper is available for a future mounted progress
screen and stops on terminal/inconsistent state; no observer/timer exists globally.

Create success invalidates the parent's workflow lists. Start acceptance invalidates
that workflow's scopes and its parent's lists. POST retry is explicitly false, even
if application defaults change. A WeakMap keyed by the existing QueryClient guards
concurrent starts for one run across mutation instances; duplicates fail immediately
without a waiting scope/queue. It is a UI safeguard, not backend admission authority.
Callers must preserve a creation key for the same user action after uncertain delivery.

## Availability is observed, not guessed

No readiness endpoint, OpenAPI probe, test workflow creation or provider probe is added.
availability.ts only classifies outcomes of real requested operations:

| Evidence | Safe observation |
| --- | --- |
| Successful GET / resource-specific 404 | API observed; execution unknown |
| Successful create | Creation observed; execution/claim still unknown |
| Start 202 | Execution task accepted at that instant; not completed/qualified |
| Security 403 | Access denied; cannot infer flags |
| Execution 503 | Execution unavailable; no permissive fallback |
| Endpoint 404 / method 405 | Route unavailable; disabled/read-only cannot be distinguished conclusively |
| Network/cancel/malformed response | Unknown |

The existing API cannot safely distinguish all four configuration/readiness states
before an authorized user action. GETs cannot prove a writer exists; OpenAPI registration
does not prove startup qualification. Future screens must show this uncertainty and
react to actual outcomes. Exact capability discovery requires a separately reviewed
contract; no misleading execution-ready boolean is introduced.

## Verification and remaining scope

Frontend tests use mock fetch and existing QueryClient conventions for serialization,
all expected statuses, errors/cancellation, nulls, trust, privacy, duplicate mutations,
cache invalidation and query cancellation. Proxy guard tests preserve legacy paths.
Backend tests use real owned HTTP sockets, migrated temporary SQLite and fake providers
to check navigation/assets, reserved paths, policy rejection and unchanged workflow flow.
An additional Chromium test exercises the real built SPA and same-origin workflow API;
the original 16 Vite/isolated-backend E2E tests are retained.

The npm E2E/UI scripts build first through pretest hooks, including in existing CI;
direct `npx playwright test` requires a prior build. Windows local qualification can
select the existing private runtime:

```powershell
$env:PROMPTFORGE_E2E_PYTHON = 'C:\Users\sinan\AppData\Local\PromptForge\sqlite-qualification\backend-venv\Scripts\python.exe'
npm --prefix frontend run test:e2e
```

An explicit invalid interpreter path fails; there is no fallback. Without an override,
E2E retains backend/.venv (including the CI-selected qualified interpreter). The legacy
test backend forces both workflow flags off even if inherited; the new SPA test uses
the production verified startup with scripted fake composition and its own disposable
database/port. Neither test uses the development database or real provider credentials.

M5F-4B/4C/4D still own creation controls, progress polling UX and results presentation.
One trusted local process/instance remains mandatory. Public deployment, proxies,
containers, additional listeners, authentication, durable delivery/replay and real-model
quality are not qualified by these offline tests. No existing optimizer, migration,
model, persistence transaction or workflow security policy is changed.
