# Persisted workflow progress and safe polling — M5F-4C

M5F-4D adds [results, evaluation history and comparison](frontend-workflow-results.md)
via **Sonuç ve Geçmişi Görüntüle**. Its explicit `view=result` URL state is refresh-safe.
The progress poller and accepted/unknown start locks are unchanged; ordinary progress
still does not retrieve content-bearing history or terminal results.

## Reaching the monitor

Prompt request details retain the existing M5F-4B save/configure/create/start flow and
add one **Workflow İlerlemesi** section. After a submitted start, that section follows
the same workflow ID. **Canlı İlerlemeyi Görüntüle** and **İlerlemeyi Görüntüle** point
to the existing page section, not an unimplemented route. Selection reveals the monitor
once without animation; subsequent polling never scrolls or changes focus.

Selection uses `?workflow=<UUID>` on the existing `/prompt-requests/{id}` route. Reload,
Back/Forward and shared detail URLs retain the selected identity. An accepted/unknown
M5F-4B session attempt is the fallback only when no explicit selection exists. Selecting
another run never alters that attempt or authorizes execution.

**Kayıtlı Workflow’ları Görüntüle** explicitly loads the existing parent-scoped list.
Each page requests 20 metadata entries, with the backend's paired timestamp/UUID cursor.
Only the current page is displayed; there is no recursive/all-history download. Previous
and next controls retain cursor navigation without changing the selected run. List rows
are not integrity-validated results or restart authorization. A selected run outside
the current page remains pinned. Absent/invalid/ambiguous IDs make no progress request.

The existing API enforces the PromptRequest assertion; the frontend additionally rejects
progress/history/list responses whose identities disagree with the requested scope.
Query keys include workflow and parent identity. No placeholder from another run is
displayed; leaving/switching detaches the observer and consumes AbortSignal cancellation.

## One controlled polling owner

WorkflowProgress mounts one TanStack Query observer built from workflowProgressQuery.
It calls getWorkflowProgress with a relative scoped GET. There is no independent
setInterval, SSE, WebSocket, background worker or POST in the progress components.

- The initial visible read is immediate. Healthy nonterminal state is read about every
  **2 seconds**, scheduled after the preceding read finishes. In-flight reads are not
  overlapped by another polling cycle.
- Polling requires a visible document and an intersecting progress section. A hidden
  tab, offscreen section or explicit pause disables automatic reads. Returning to a
  visible active section permits one fresh read, without an additional focus/reconnect
  refetch burst. An already-started GET may finish while hidden; it does not start work.
- **İzlemeyi Duraklat** keeps the snapshot; **İzlemeyi Sürdür** resumes safe reads.
  **İzlemeyi Kapat** unmounts the observer. Navigating away or changing IDs cancels the
  old observation. None of these controls cancels a backend task.
- COMPLETED, FAILED and CLARIFICATION_REQUIRED stop the automatic interval. Integrity
  findings, invalid/truncated history or a known inconsistent operation summary also
  withhold trusted completion and stop automatic observation.
- GETs used by progress, metadata and listing have a **10-second deadline**. Navigation
  cancellation remains distinct from a deadline/network failure. Deadline timers are
  cleaned up; they are not polling loops.
- The first failed automatic read stops automatic traffic for that mounted query,
  including across visibility changes. No aggressive retry/backoff loop is created.
  **Şimdi Yenile** makes one GET; a successful manual read resumes healthy active polling
  unless explicitly paused. Cancelling a read produces no misleading failure alert.

Focus/reconnect refetch and query retry remain disabled. Existing query keys/cache and
mutations are reused. Inactive read data is not persisted to browser storage. Creating
or starting still requires the separate explicit M5F-4B controls.

## What the display proves

| Stored state | Turkish presentation / meaning |
| --- | --- |
| PENDING | Başlatılmayı bekliyor; no durable claim is visible, not a guaranteed queue |
| GENERATING | Prompt hazırlanıyor; last persisted preparation phase |
| EVALUATING | Prompt değerlendiriliyor; last persisted evaluation phase |
| REVISING | Prompt iyileştiriliyor; last persisted revision phase |
| COMPLETED | Yürütme tamamlandı; technically terminal, independently of quality |
| FAILED | İşlem başarısız; persisted failure, never inferred from a browser error |
| CLARIFICATION_REQUIRED | Ek açıklama gerekiyor; terminal, no questions are fabricated |

The view uses only current_round, completed_rounds, max_evaluation_rounds and the
existing timestamps/selected-evaluation/integrity fields. Zero completed evaluations
is different from an unknown score. A missing score remains **Bilinmiyor**, never a
fabricated zero; an existing selected score/quality is explicitly the last verified
evaluation, not a promise that execution succeeded or the final result passed.

Configured rounds are an upper bound, not a progress denominator or promised work.
An early one-round completion is valid. No elapsed-time percentage, live provider
activity, estimated duration, token count or intermediate score is invented. The
browser's last successful-read time is labeled as such, not a server updated_at or
worker heartbeat. A supplied finished_at is separately labeled as a stored fact.

The backend exposes no reliable current worker heartbeat, lease expiry, provider-call
liveness or general last-update timestamp in the public progress DTO. A long-lived
PENDING/active snapshot cannot diagnose why work has not advanced. Inspect/refetch;
never rotate claims, restart, replay or infer execution readiness from a GET.

## Metadata summaries and integrity

**İşlem Özetini Görüntüle** explicitly fetches metadata-only `/history`. It is a single
snapshot with its own manual refresh, not a second polling interval. Mapped Architect/
Critic operation types, stored operation states, round and start timestamps are shown.
RUNNING/incomplete/unresolved records explicitly mean completion is unverified; during
an active workflow this can be normal. They are not proof of live paid-provider activity
or recovery permission. A terminal record with unfinished operations is not presented
as trusted completion. Known integrity findings always take precedence over approval.

Metadata may be older than the latest progress read and is labeled as a one-time
snapshot. When it reveals inconsistent history, refresh the metadata safely before
trusting completion. Ordinary monitoring does not fetch content history, final result
or recovery endpoints. M5F-4D's explicit result view retrieves terminal results and
metadata, and its separate load action retrieves history content. Clarification
editing remains deferred. No nonexistent result route is linked.

## Submission uncertainty and errors

202 means the supervisor owns a local task; the first persisted GET can still be
PENDING. The monitor explains that gap and does not treat it as an immediate error.
A prolonged PENDING message offers read-only refresh, never another start POST.

Progress components never write attempt markers or invoke create/start. M5F-4B's unknown
and accepted locks remain unchanged. A subsequent PENDING GET, frontend deadline or
visibility change cannot clear an uncertain-start lock. A completed progress view does
not itself enable new execution; the existing explicit configuration/metadata checks
remain authoritative. Reload and navigation only restore/read the selected ID.

403/404/409/422/429/503, network failures and malformed responses use sanitized messages
and approved request IDs. A stale successful snapshot remains labeled as the last
read when refresh fails; network failure never becomes a persisted FAILED workflow.
HTTP availability is not execution qualification. Raw errors, prompt/critique content,
provider identifiers and fencing data are neither logged nor put into progress errors.

Same-origin relative URLs, no-store, Host/Origin/forwarding checks, actual listener
ownership and default-off flags remain unchanged. Use the [verified local SPA setup](frontend-workflow-integration.md).
Vite is still not a workflow execution proxy; public/proxy/container deployment and
automatic recovery are unsupported.

## Accessibility and verification

The existing Turkish Tailwind UI gains status-specific colors, native selector/buttons,
visible focus rings, wrapping identifiers and mobile layout. A polite atomic live region
contains only durable status/counter changes. Browser refresh times and transient GET
activity are outside it, so identical snapshots are not announced every two seconds.
There are no synthetic percentage bars or mandatory animations, including for reduced
motion. Loading controls are bounded by GET deadlines; errors stop automatic traffic.

Deterministic fake-timer/component tests cover timing, non-overlap, timeout, unmount,
offscreen/hidden visibility, pause/resume, terminal states, stale errors, identity scope,
cursor navigation, late responses, metadata separation and unchanged live announcements.
All M5F-4B submission tests remain in the regression suite.

The isolated Chromium harness adds fake early/three-round/failure/clarification profiles.
Provider operations pause on a parent-owned stdin barrier, not arbitrary sleeps or an
HTTP control endpoint. Browser tests cover reload during execution, stored Architect/
Critic transitions, disconnect/recovery and a lost 202 receipt without duplicate dispatch.
Every case uses a migrated disposable database, an exclusive test listener and the
qualified private Python/SQLite runtime. No production agent, scoring, persistence,
security, model or migration behavior is changed and no paid provider is called.

These are controlled polling observations, not instantaneous server push, durable HTTP
delivery, exactly-once billing or real-model quality evaluation. M5F-4D owns the detailed
results/history interface on the existing selected workflow page.
