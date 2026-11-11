# Local MVP troubleshooting

Use the [setup guide](local-mvp-setup.md) and [configuration/mode reference](local-mvp-operations.md).
All diagnostics use the exact qualified interpreter and a reviewed dedicated database.
Keep prompt bodies, keys, Critic evidence, raw SQL/provider exceptions and real env files
out of shared logs/screenshots. A sanitized `X-Request-ID` can help correlate failures.

| Symptom | Likely cause | Safe diagnostic step | Recommended resolution |
| --- | --- | --- | --- |
| Wrong Python or preflight refusal | PATH/venv selected another executable | Run `$backendPython -m app.db.verify_ci_runtime` from `backend/` | Select exact approved 3.12.16, recreate/sync only the intended env, rerun preflight; no silent fallback |
| Connected SQLite unsafe/unverified | Interpreter build embeds an affected/unknown library | Read both connected versions printed by preflight | Stop workflow qualification; obtain a reviewed safe build. Do not replace DLLs, monkeypatch or force DELETE mode |
| Activation script denied | PowerShell policy | Inspect the chosen executable path without changing policy | Invoke `.venv\Scripts\python.exe` explicitly; activation is optional |
| Module/package missing | Unsynced or wrong environment | Confirm executable and frozen sync result | Run documented `uv sync --frozen --no-python-downloads --python ...` in the intended install; do not edit the lockfile |
| uv discovery warns about a missing system Python | Broken unrelated PATH entry | Use the approved executable directly | Avoid that PATH entry; repair system tooling separately, never select it as fallback |
| npm install/build fails | Wrong Node, missing packages or unavailable cache/network | Check `node --version`, `npm --version` and lockfile install output | Use qualified Node/npm and `npm ci`; report unavailable downloads rather than changing dependencies |
| Chromium executable missing | Browser not installed for current account | Read Playwright's missing-browser message | In `frontend/`, run `npx playwright install chromium`; Linux needs separately qualified system dependencies |
| Missing table / migration version error | Wrong DB or migrations not applied | Confirm the exact dedicated URL and run `$backendPython -m alembic current` | Stop users, back up retained data and explicitly migrate the reviewed target; do not initialize over personal data blindly |
| Default development DB refused | Dedicated runner blocks `backend/data/promptforge.db` and aliases | Review `--database` path | Provision a new dedicated migrated SQLite file; do not bypass the refusal |
| DB does not exist / read API 503 | Missing file, permissions or invalid schema | Confirm chosen file exists, is readable and has current revision | Correct the explicit target/permissions; workflow GETs do not create it |
| SPA startup rejects missing build | No `index.html`/`assets/` in trusted dist | Inspect `frontend/dist/` after successful build | Run `npm ci` then `npm run build`; supply the actual compiled directory |
| SPA path exists but is wrong | Source directory, stale build or nondefault API prefix | Check `--frontend-dist`, trusted build contents and `/api/v1` prefix | Use the compiled immutable build; rebuild/restart; never point at source/uploads/secrets |
| Address already in use | Another server owns the port | Read-only `Get-NetTCPConnection -State Listen -LocalPort 8000` and identify owner | Stop your known server cleanly or choose an unused loopback port; never kill an unidentified process or enable port sharing |
| Workflow route 404/405 | Feature off/API-only mode, missing resource or wrong method | Check intended flags before startup and actual route/ID | Use the mode matrix; a 404 alone does not diagnose configuration; do not probe by starting work |
| Create/start 503 | Ordinary Uvicorn, lost listener, runtime/persistence failure or shutdown | Inspect sanitized `error_code` and dedicated startup output | Use the documented owned-listener entry point with safe DB/runtime; no permissive fallback |
| Listener qualification fails | Wrong binding, reload/workers, proxy/SSL/root-path/inherited socket | Compare exact CLI to setup; inspect owned port | Use one dedicated loopback process with supported options; do not disable proof checks |
| Host/Origin or forwarded-header 403 | Different host spelling/port, cross-site access or proxy | Compare browser URL and API origin; inspect sanitized category | Open the direct `127.0.0.1` origin consistently; remove unsupported proxy deployment, not the checks |
| Vite workflow 403 | Deliberate development proxy guard | Read `workflow_development_proxy_denied` | Build the SPA and serve through qualified backend; Vite remains ordinary UI development |
| Health is OK but execution unavailable | Health proves only HTTP service availability | Read mode/startup diagnostics; no generation probe | Qualify explicit runtime and opt-in separately; health/OpenAPI are not execution readiness |
| GET timeout/network error | Request exceeded ten-second UI read bound or connection lost | Read persisted status using manual scoped GET | Refresh reads explicitly; read errors must never trigger execution POST/replay |
| OpenAI configuration failure | Missing/blank backend key or unsupported provider setting | Run no-generation provider construction check privately | Correct protected backend settings; do not print keys or use VITE variables. This does not validate account access |
| Stock fake workflow FAILED | CLI fake has no scripted responses | Inspect safe persisted failure/terminal state | Expected safe failure; use deterministic scripted E2E for successful offline examples, not a fabricated fake response |
| Database busy/locked | Another writer/maintenance/process owns the file | Identify known processes and active work; no row edits | Wait for existing work, close known users safely and inspect; no blind transaction/provider retry or journal downgrade |
| Capacity 429 | One local owner already executing/draining | Read current workflow and Retry-After | No queue exists; wait/inspect, then choose an explicit eligible new action only when safe |
| PENDING/in-progress or unknown start | Accepted task may not have claimed yet; response may be lost | Use `Kayıtlı Durumu Kontrol Et`/progress GET; inspect read-only recovery if needed | Preserve unknown-start lock. PENDING, expired lease or missing Evaluation does not authorize duplicate start |
| Shutdown stuck | Provider/task ignored cooperative cancellation | Preserve diagnostic evidence and readback; verify process status | Operator intervention may be needed. Do not claim remote cancellation, dispose underneath live work or reset ownership records |
| Integrity inconsistency / copying withdrawn | Stored graph mismatch or newly inconsistent content history | Inspect retained metadata/readback; preserve original records | Investigate; do not manually repair/delete rows. After externally reviewed resolution, `Sonucu Yenile` requires a fresh valid result GET |
| Close/reopen or workflow switch stays blocked | Scoped integrity revocation correctly survived remount | Inspect same parent/workflow and explicit result-read outcome | Cached success, canceled/error GETs or other workflow reads cannot restore trust; do not clear storage/cache to bypass investigation |
| FAILED result | Persisted execution failed, possibly with earlier evaluated history | Read safe failure/actual history | It is not successful optimization. Do not replay uncertain external work or invent a final version |
| CLARIFICATION_REQUIRED | Essential missing/conflicting information | Read actual persisted questions | No final prompt is promised; clarify input for a later explicitly chosen request. No resume/edit endpoint exists |
| Result not yet available | Workflow not terminal | Read progress; 409 `workflow_not_terminal` is not FAILED | Wait/inspect through GET; do not submit another start |
| Null or not-qualified score | Assessment absent/unassessable or gates unmet | Inspect supplied nine criteria/quality outcome | Keep null distinct from zero and technical completion distinct from qualification; never recalculate in UI |
| Clipboard failure | Browser permissions/API rejection | Read fixed UI error; use trusted direct local browser context | Retry explicit copy only for currently verified text; no sensitive error dump or integrity bypass |
| Frontend/backend mismatch | Stale dist, different checkout/process or wrong legacy API base | Confirm process revision, rebuild output and same origin | Stop known server, rebuild from same reviewed checkout and restart; do not broaden parsers/CORS to accept incompatible responses |

Never delete DB/WAL/SHM files, change fencing/leases, weaken schema validation or replay
unknown paid work as troubleshooting. [Recovery](workflow-readback-recovery.md) is
diagnostic only. If a P0/P1 blocker requires behavior/architecture changes, stop and
request review; do not expand a documentation package into an unreviewed redesign.
