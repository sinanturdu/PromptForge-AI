# Workflow results, evaluation history and comparison — M5F-4D

## Access and read ownership

On saved prompt details, select a workflow in the existing **Workflow İlerlemesi**
monitor, then choose **Sonuç ve Geçmişi Görüntüle**. This opens one dedicated result
section without another route or application store. The existing URL gains
`?workflow=<UUID>&view=result`; reload and direct navigation restore the selection
and open result view. No private text is placed in the URL. Closing the view removes
only `view`; workflow selection and the M5F-4B start safeguards remain intact.

The existing parent-scoped workflow selector and 20-record cursor pages are reused.
Changing workflow IDs remounts the keyed monitor subtree, aborts old reads and drops
inactive content queries. No placeholder from another workflow is displayed. All GETs
include the PromptRequest assertion; response identities are independently checked.
Selecting, reloading, comparing, copying and manually refreshing never create/start
a workflow. A read result does not unlock an accepted or unknown start attempt.

The result view explicitly opts into sensitive terminal-result retrieval. Opening
ordinary progress alone still fetches neither result nor content history. A direct
result URL is also an explicit request to view the result.

## Four separate facts

The backend remains authoritative for lifecycle, selected evaluation, quality gates
and persistence integrity. React does not implement scoring, weighting or candidate
selection. The nine production rubric keys were checked against `scoring/rubric.py`.

| Fact | Presentation |
| --- | --- |
| Execution | The seven existing persisted states and their Turkish labels |
| Result integrity | Validated terminal response and matching parent/workflow/version/evaluation references; known integrity findings override an older successful response |
| Quality | `qualified`, `not_qualified`, `unassessable`, or explicitly unknown when metadata supplies null |
| Content availability | Only the actual selected rendered prompt; absent content is never reconstructed from canonical fields or notes |

COMPLETED is technical completion, not qualification. A high-looking score does not
override the backend quality outcome. FAILED is not the same as not-qualified.
An active/PENDING workflow displays **Nihai sonuç henüz hazır değil** without requesting
the terminal result. HTTP 202 remains an admission acknowledgment, not a result.

FAILED and CLARIFICATION_REQUIRED have sanitized explanations and no final-prompt
copy action. They may retain earlier evaluated candidates, inspectable through the
explicit history-content view. Actual clarification questions from the terminal API
are rendered as text; absent questions stay absent. Editing/answering them and starting
another workflow are not added here.

## Best prompt and copy

For a valid COMPLETED result, the candidate must match the backend-selected version
and evaluation. Its parent/workflow, round, stored version pointer and evaluation
quality references are checked before presentation. No latest-round or highest-score
heuristic is used; an earlier evaluation can be selected as best.

**Prompt'u Kopyala** writes only `rendered_prompt` to the browser Clipboard API after
an explicit click. Canonical fields, Architect notes, rationale and evidence are not
appended. The action requires available nonempty content and valid read/integrity state;
it is withheld on invalid/mismatched responses and known integrity findings. Metadata
refresh temporarily disables copying; result refresh withholds the displayed candidate
until the read succeeds. Clipboard success/failure uses a polite live region and fixed
messages; raw clipboard exceptions are discarded.

A trustworthy not-qualified or unassessable COMPLETED candidate is copyable, with a
clear quality warning. Trust in stored content is distinct from quality approval.
The application passes the exact string, without trimming or newline conversion.
Windows' native clipboard transport can return CRLF for LF input; browser tests verify
that platform behavior separately, and component tests verify the exact writeText input.

Prompt/code/markup content is plain React text inside wrapping, bounded-height,
keyboard-focusable preformatted blocks. No Markdown/HTML interpreter, unsafe HTML,
syntax execution or generated links are introduced. Long unbroken strings wrap.

## Critic evaluation

The dedicated panel maps all nine required contract keys:

| Key | Turkish label |
| --- | --- |
| intent_fidelity | Niyete sadakat |
| clarity | Açıklık |
| completeness | Eksiksizlik |
| structure | Yapı |
| context_and_role | Bağlam ve rol |
| constraints_and_scope | Kısıtlar ve kapsam |
| output_format | Çıktı biçimi |
| specificity | Uygun ayrıntı |
| consistency | Tutarlılık |

Each criterion shows its supplied score/rationale and expandable evidence. Overall
scores retain their decimal JSON strings verbatim; null is **Değerlendirilemedi**,
while numeric zero is shown as zero. Missing required criteria reject the response,
rather than receiving fabricated defaults. The grid has no weighted arithmetic or
visual bar that implies unsupported precision. Supplied gate booleans/nulls are shown
separately without recalculation.

Issue descriptions, recommendations, criterion, code, supported/uncertain judgment,
severity and evidence come only from the approved Critic assessment. Uncertain
judgments are labeled distinctly; severity/counts are never invented. These sensitive
texts are presentation data, never operational logs or copy-ready prompt additions.

## Metadata first, content on demand

Opening results first reads `/history` independently of `/result`. Its bounded server
snapshot shows actual evaluation rounds, version references, persisted decimal totals,
quality and completion timestamps. Empty, incomplete and staged-operation cases are
explicit; they are not invented completed evaluations. The configured round limit does
not promise all rounds will run. An early terminal completion is explained.
If the view was open during execution, a validated terminal result triggers one scoped
refresh of an older metadata snapshot. The revision is marked before the read; a failed
refresh does not start a retry loop. This is not a second polling scheduler.

**Prompt Sürümlerini ve Değerlendirmeleri Yükle** mounts the separate `/history/content`
query only after a click. It provides the validated source, evaluated prompt versions
and detailed Critic assessments. Closing content unmounts that observer. Manual GET
refresh can load newer committed history; there is no second polling schedule.

The comparison uses two explicitly selectable texts: the approved original source and
available persisted rendered versions from this workflow. It defaults to original versus
backend-selected version when both are available. Desktop columns stack on mobile.
Text equality is exact, including whitespace; both full texts remain available in
scrollable blocks. The UI neither generates a revision nor compares unrelated canonical
fields as if they were rendered text. Missing versions reduce the interface truthfully.
Staged/unevaluated candidate bodies are not exposed by the public content projection,
so they are not offered as versions.

## Renderer identity — M6-3A

The selected result shows the stored target family, exact renderer name and renderer
version next to the evaluated prompt. Each verified evaluation in the history summary
shows the identity of its linked persisted prompt version. The comparison shows the
stored identity beside each rendered version and labels the original prompt separately.

The target describes where the user plans to use the prompt. The renderer formats the
candidate text. The LLM provider performs generation or evaluation. New OpenAI-target
workflows use `OpenAIPromptRenderer`. Historical OpenAI-target workflows may show
`GenericPromptRenderer` because that renderer produced the text evaluated at the time.
Generic, Claude and Gemini targets currently use the Generic renderer.

Only the exact selected text evaluated by the Critic is copied. A different format
would need its own evaluation. When a version link or renderer identity cannot be
verified, the summary can retain stored evaluation records for inspection without
claiming a verified renderer. Detailed history requires a unique version, a linked
evaluation with its completed Critic operation, and the associated assessment.
Result, summary and detailed history must agree on shared version ownership,
renderer identity and evaluation references. Shared rendered text must also match.

A same-revision summary must contain the selected result's version and evaluation.
An older summary may predate that version. The view can refresh it once through the
existing bounded read. A newer summary withholds result display and copy until a
manual result read catches up. Contradictions in shared records revoke approval
regardless of revision. Typed result-integrity errors also revoke approval for that
prompt request and workflow across panel reopening. Diagnostic records are marked
unverified while approval is revoked.

Recovery requires an explicit fresh validated result GET that agrees with current
history and has no intervening integrity failure. In comparison, an untouched pane
follows late backend selection when the selected version is available. An explicit
choice remains selected across content refreshes while its version is available.

## Query lifetime, errors and privacy

Existing Query keys keep progress, metadata, content and result separate and scoped
to workflow/parent. Progress remains the only two-second polling owner. Result/content
reads have the existing **10-second deadline**, consume AbortSignal and have no retry,
focus/reconnect refetch or interval. Successful content stays in memory while the
view needs it; inactive read queries use `gcTime: 0`. No browser persistence adapter is used.
Existing create/start invalidations remain scoped to the same workflow/parent keys.

M5F-5A's independent-review correction stores content-integrity revocation in a tiny
normalized parent/workflow QueryClient record (flag/revision only, infinite GC lifetime,
no network function/timer). Synchronous subscription withdraws approval before paint.
It survives closing/reopening results, parent remount and switching away/back; another
workflow is unaffected. Only an explicit new successful terminal-result GET passing
existing relationship/contract validation clears the same observed revocation.
The Query data-update counter rejects cancellation returning reverted cached data;
an older pending success also cannot erase a newer integrity observation. Failed,
pending, timed-out, malformed, metadata/progress and other-workflow reads do not clear it.
Recovery closes and evicts the rejected content history; the next explicit load fetches
an actual new snapshot. Browser reload discards private caches/guards and queries the
backend again. No disk/cross-tab guard or automatic repair/replay is introduced.
See the [P1 audit evidence](m5f-security-regression-audit.md).

409 `workflow_not_terminal` is a not-ready explanation, not a persisted failure.
409 `workflow_integrity_inconsistent` explicitly withholds trusted content/copying.
403/404/422/429/503/500, malformed JSON/contracts, timeouts and disconnects use the shared
sanitized error model. Errors do not mutate persisted lifecycle or dispatch another
POST. Read-only manual refresh is available; cancellation does not produce false alerts.
Previously read metadata remains visible for investigation, labeled unverified when
integrity fails. No records are hidden from the database, deleted, repaired or replayed.
Recovery diagnostics are not needed for this presentation and no recovery endpoint
or takeover action is introduced.

Same-origin relative `/api/v1`, no-store, default-off flags, qualified connected
SQLite, actual listener ownership and loopback/Host/Origin/forwarding protections are
unchanged. See the [local integration setup](frontend-workflow-integration.md).
No private content enters storage, URLs, console output, analytics or error messages.
Only the explicit clipboard action exports the selected text. No credentials,
dependencies, provider implementations, production endpoints, models or migrations change.

## Verification and limits

Focused offline component tests cover the three quality states, all criteria, zero/null/
decimal scores, supplied issues, exact copying, clipboard rejection, missing versions,
metadata/content separation, scoped/late responses, timeout/cancellation, malformed
responses, HTTP errors, integrity precedence, privacy and read-only retries. Existing
creation and polling suites remain regression requirements.

The original M5F-4D Chromium scenarios use real FastAPI, migrated disposable databases and controlled
fake providers: desktop/mobile qualified results and clipboard/comparison/reload;
not-qualified earlier-version selection and two-version comparison; unassessable early
completion; switching to another workflow; failed and clarification results; and
integrity rejection with actual evaluation metadata retained. Long unbroken/multiline
text is included. The integrity fixture clears one selection pointer only in its owned
temporary database, using the qualified private Python. It adds no HTTP control route.
M5F-5A adds content-history revocation and retained-cache remount/recovery cases; the
current run counts are dated in the [release checklist](local-mvp-release-checklist.md).
The restoration helper models external repair only in that owned disposable fixture;
it is not an application repair mechanism.

These tests establish contracts and UI behavior, not real-model quality or exactly-once
provider billing. General deployment, recovery execution, clarification editing,
semantic/inline diffing, canonical editing and unevaluated version content remain out
of scope. No paid LLM calls are used.
