# Prompt creation and explicit optimization — M5F-4B

M5F-4C adds the [read-only progress monitor](frontend-workflow-progress.md) alongside
these controls. Its polling and URL selection do not alter creation identity, start
authorization or uncertain-submission locks. The inventory below records M5F-4B behavior.

## User flow

NewPromptPage keeps the existing form, validation, preferences and Save action. Saving
only creates a PromptRequest and opens its existing detail route. It never creates a
workflow, starts optimization or probes execution readiness.

PromptRequestDetailPage now includes an OptimizationPanel for the loaded saved ID:

1. **Optimizasyonu Yapılandır** expands an accessible inline section; no request is sent.
2. Select **En fazla değerlendirme turu**, default 3, with only 1/2/3 supported. Client
   validation rejects an invalid value before POST. There are no provider, protected-span,
   token, lease or revision controls. The backend continues using its reviewed safe defaults.
3. **Workflow Oluştur** sends the existing M5F-4A create mutation for that saved parent.
   A 201 acknowledges creation; a 200 shows that the existing keyed workflow was found.
   The ID is retained, and nothing starts automatically. Creating an optimization never
   duplicates the saved PromptRequest.
4. **Optimizasyonu Başlat** explicitly reads scoped metadata, checks fresh eligible
   PENDING state and submits the required public expected_revision through the existing
   supervised start mutation. The revision is not displayed as a configuration field.
5. A 202 shows **Optimizasyon isteği kabul edildi**, its workflow ID and an explanation
   that asynchronous acceptance is not completion or quality qualification. A history
   link targets the real /history route; no unimplemented progress route is introduced.

The section uses the existing Tailwind layout and ErrorAlert, Turkish labels, native
keyboard controls, focus rings, disabled/loading actions, aria-expanded/controls,
labelled validation, polite status announcements and wrapping identifiers. It adds no
modal chain or global redesign. Saving/viewing/history/navigation remain available
when the workflow feature is disabled or denied.

## Creation identity and navigation safety

attempts.ts maintains one session-scoped attempt per saved PromptRequest. A key is
generated only on the first explicit valid creation action. The immutable round count
and key are reused for every retry, including after a lost response or page reload.
Configuration locks once that attempt exists, so a retry cannot silently change the
configuration under the same key. Creating again is never an automatic error reaction.

sessionStorage holds only `{version, key, rounds, workflowId, start}` under an ID-based
namespace. The non-sensitive round count is necessary to repeat the exact creation
configuration. No prompt/context/preferences, credentials, response content, telemetry,
quality score, execution token or revision is stored. Shape/size validation and
compare-before-write prevent stale receipts from replacing a newer local attempt.
The data is a local submission safeguard, not an API response cache or authorization.

Storage failure/corruption blocks new workflow mutations before dispatch; it does not
block the ordinary PromptRequest pages. Browser session storage is not durable delivery
or coordination across independently opened tabs/processes. Backend idempotency,
scope checks, unique constraints, reservations and fencing remain authoritative.

An explicitly chosen **Yeni Workflow Yapılandır** resets the local attempt only after
a manual GET confirms trusted terminal state with no incomplete/unresolved operations.
The next explicit create then gets a new key. This is a new workflow, not resumption or
automatic replay of the previous workflow. No reset action is offered for uncertain,
active, truncated or inconsistent history.

## Start outcomes and recovery limits

The UI writes an unknown-submission marker **before** dispatching start. A network,
cancellation, malformed response or HTTP 500 can mean that the server accepted work;
the marker remains across navigation/reload and the start action stays disabled.
Even a subsequent PENDING GET cannot prove that an admitted task will not claim later,
so it does not unlock another start. Cancelling/leaving the browser does not cancel
an accepted backend task. There is no automatic POST retry.

Recognized 403/404/405/409/422/429/503 refusal responses are kept distinct from an
unknown outcome. A user may explicitly retry a definitively rejected start only after
another fresh eligibility read. Capacity is not queued. A 409 also performs one safe
metadata read where possible to show the persisted state. HTTP 500 and unrecognized
error bodies do not authorize this retry path.

Preflight read failure is different: the UI explicitly says that no start POST was
sent. Navigation during that preflight prevents a late POST. Synchronous action locks,
mutation loading state, the shared M5F-4A duplicate-start guard and the persisted
submission marker prevent conflicting local actions; backend admission still decides
the actual winner.

**Kayıtlı Durumu Kontrol Et** makes a single scoped metadata GET. It shows an observed
stored lifecycle state and an integrity/uncertainty warning where needed. It never
starts work, renews a lease, takes over, repairs records or fetches content history.
The read contract does not expose lease expiry, so the UI does not invent that diagnosis.
There is no polling, live progress chart, quality verdict or detailed result rendering.

## API integration and availability

useOptimization uses the existing QueryClient, workflowMetadataQuery and create/start
mutation options. Existing invalidations remain in those definitions: create invalidates
the parent's workflow lists; start invalidates the run scopes and parent lists. Reads
are disabled until an explicit inspection/start action and consume cancellation signals.
No new API client, query namespace, background worker or cache persistence is introduced.

ErrorAlert receives fixed, sanitized WorkflowClientError messages and approved request
IDs only. Invalid configuration, missing records, conflicts, denied security, unavailable
startup/capacity and network/unknown outcomes are represented without exposing raw error
bodies, provider identities or internal tokens. A route-level 404/405 is described as
possibly disabled/read-only, not diagnosed conclusively. GET/OpenAPI registration never
claims execution readiness, and no test workflow/provider probe is created.

Use the [M5F-4A same-origin setup](frontend-workflow-integration.md): built SPA served by
the existing verified local backend, both flags explicitly enabled, safe private runtime
and an existing non-development migrated database. Vite's workflow proxy denial remains
unchanged; ordinary development can still save/view PromptRequests. Production backend
contracts, defaults, APIs, listener/security checks and persistence are unchanged.

## Verification and next packages

Component tests use MSW and the existing application renderer. They cover the original
save path, default/invalid configuration, exact request bodies, 201/200 replay, stable
retry identity across reload, double clicks, preflight navigation, explicit 202, uncertain
submission locks (including PENDING readback), recognized refusal retry, terminal and
inconsistent states, unavailable features, storage failure and private-data exclusion.

The existing same-origin E2E lifecycle is extracted into local-spa-fixture.ts so the
original API browser scenario and new desktop/mobile UI scenarios each get a fresh
database and scripted fake provider. The Python test helper asks the owned listener
for port zero and announces the actual port; no existing server is reused. This avoids
Linux TIME_WAIT rebinding when multiple same-origin cases run sequentially without
changing production socket policy or adding delays/retries. Runtime, identity and
exclusive listener checks remain in place. The original E2E assertions are retained.

M5F-4C now supplies live progress/polling UX; M5F-4D still owns detailed results/history. Exact
capability discovery, public/multi-user deployment, durable HTTP delivery and automatic
recovery/replay remain outside this package. Offline fake tests establish contract and
interaction behavior, not model quality or exactly-once external billing.
