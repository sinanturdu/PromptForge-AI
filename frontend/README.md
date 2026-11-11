# PromptForge AI — Frontend

Turkish React 19 UI with Vite 8, strict TypeScript, Tailwind CSS 4, React Router and
TanStack Query. Save/search/filter/detail pages and explicit optimization creation,
progress, results, nine-criterion evaluation, history, comparison and copying are implemented.
Saving does not start optimization.

## Install, develop and build

Use [Windows local setup](../docs/local-mvp-setup.md) for the qualified backend and
dedicated database. From `frontend/`:

```powershell
npm ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed.' }
npm run dev
```

Vite binds `127.0.0.1:5173` by default and proxies ordinary `/api` requests to
`http://127.0.0.1:8000`. **Workflow proxy paths deliberately return 403
`workflow_development_proxy_denied`.** This is PromptRequest UI development, not execution.

```powershell
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
```

Output is `frontend/dist/` (`index.html`, `assets/`). Use the [dedicated backend startup](../docs/local-mvp-setup.md#start-the-same-origin-local-application)
with `--frontend-dist` for the same-origin workflow SPA. `npm run preview` is static
inspection only. Rebuild/restart after changing frontend or backend.

| Variable | Default / effect |
| --- | --- |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000`; development proxy only; cannot authorize workflows |
| `VITE_API_BASE_URL` | Empty; legacy PromptRequest base only. Keep empty in every loaded Vite mode file for local SPA. Workflow client always uses relative `/api/v1`. |

All `VITE_*` values can enter browser builds. Never put credentials or private prompts
there. No frontend variable selects a backend provider or enables execution.
See the [configuration reference](../docs/local-mvp-operations.md#configuration-reference).

## Routes and behavior

| Route | Behavior |
| --- | --- |
| `/` | Save prompt, optional context and preferences |
| `/history` | Server search/filter/pagination, URL state and return navigation |
| `/prompt-requests/:id` | Saved content, configure/create/start and workflow monitor |

Results use `?workflow=<UUID>&view=result`; no private text enters the URL. The verified
SPA serves only these browser routes, favicon and allowlisted assets. There is no blanket
fallback: API/OpenAPI/docs and unknown paths retain their real responses.

- [Foundation/transport](../docs/frontend-workflow-integration.md).
- [Creation/start and unknown delivery](../docs/frontend-workflow-creation.md).
- [Progress and polling](../docs/frontend-workflow-progress.md).
- [Results, history and integrity](../docs/frontend-workflow-results.md).

One visible active monitor owns the approximately two-second GET poller. Terminal,
inconsistent, hidden, paused and failed-read states stop automatic polling as documented.
GET deadlines are ten seconds; read errors never start execution. HTTP 202 is acceptance,
not completion. Null scores stay unknown; completion and qualification remain distinct.

Content-integrity revocation is scoped to parent/workflow in the shared in-memory
QueryClient and survives result/parent remounts and switching away/back. Only explicit
fresh validated result revalidation clears it; cancellation or unrelated GETs cannot.
Recovery closes/evicts the rejected content snapshot; history must be explicitly loaded
again. Reload discards private caches and reads the backend. No result/prompt/evidence
is browser-persisted; small session submission markers are not authorization.

## Automated checks

From `frontend/`, using installed dependencies:

```powershell
npm run typecheck
if ($LASTEXITCODE -ne 0) { throw 'TypeScript failed.' }
npm run lint
if ($LASTEXITCODE -ne 0) { throw 'ESLint failed.' }
npm run test
if ($LASTEXITCODE -ne 0) { throw 'Vitest failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
```

Vitest/RTL/MSW discover only `src/**/*.test.{ts,tsx}`. Playwright discovers
`e2e/**/*.spec.ts`, uses real FastAPI/migrated SQLite and fake optimization providers.
No MSW replaces the E2E backend. Install Chromium once with `npx playwright install chromium`;
this downloads a browser, not an LLM response.

From repository root after setup selects `$backendPython`:

```powershell
$env:PROMPTFORGE_E2E_PYTHON = $backendPython
npm --prefix frontend run test:e2e
if ($LASTEXITCODE -ne 0) { throw 'Chromium tests failed.' }
```

E2E/UI scripts build first. Optional scripts: `test:e2e:ui`, `test:e2e:report`;
`npm run test:e2e -- e2e/workflow-results.spec.ts` focuses results, and `--repeat-each=3`
repeats with new databases. One worker, zero retries, failure traces/screenshots and
HTML reports are configured. `playwright-report/` and `test-results/` are ignored;
review artifacts for sensitive test content before sharing.

Legacy PromptRequest fixtures reserve 18080/15173; workflow SPA fixtures own an
OS-assigned loopback port. Both verify run identity and clean up their own resources;
neither reuses a development server. `PROMPTFORGE_E2E_PYTHON` selects an exact interpreter;
an invalid override fails. Otherwise fixtures use `backend/.venv`. Safe runtime required.
Linux CI installs Chromium with `--with-deps` and independently checks SQLite;
configured behavior does not establish remote success. See [release evidence](../docs/local-mvp-release-checklist.md).
