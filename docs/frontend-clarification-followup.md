# Clarification follow-up

Open **Sonuç ve Geçmişi Görüntüle** on a workflow that finished with
`CLARIFICATION_REQUIRED`. Under **Ek Bilgi Gerekiyor**, answer the stored
questions, then select **Yanıtlarımla Yeni İstek Taslağı Hazırla**. This opens an
editable draft on the same page. **Prompt İsteğini Kaydet** saves the draft as
a separate request and opens its detail page. Workflow creation and execution
are separate actions on that new request.

## Draft construction and validation

`PromptRequestDetailPage` passes the saved source to the workflow result view.
`ClarificationFollowup` holds answers in component state and uses
`PromptSubmitForm` for review and Save. It starts with the original raw prompt
and all saved preferences. It appends the exact stored questions and current
answers, in order, after the existing context under **Ek Açıklama Yanıtları**.
The answers are user-provided context. Returning to the answers and preparing
again rebuilds the draft from the original record.

The editor maps textarea selections back to the underlying string. Existing CRLF
and LF sequences survive edits to other spans, and new lines use the textarea's
LF representation. Submitting an untouched field sends its original string.

Blank answers are flagged inline and focus moves to the first invalid field.
The prompt and question checks use the same Unicode blank characters as
Python's `str.strip()`.
Context above the form's 20,000-character limit is available in full for
editing. The counter shows its length and Save is disabled until the draft
passes the request form's validation.

## Trust and privacy

Follow-up requires a verified terminal result, matching workflow and parent
IDs, current history metadata, the matching saved source, and one to three
nonblank questions that agree with the stored count. If detailed history is
loaded, the source and same-revision questions must agree with it. The existing
result integrity guard still applies. A confirmed contradiction between
detailed history and the source or questions blocks follow-up for that workflow
in the current app session, even after the history panel closes. The existing
read-only result remains available where its integrity permits it.

Progress, result, summary and detailed history reads can raise a revision floor
scoped to the workflow and its parent. The floor stores a number in memory and
survives cache eviction of those reads. If a valid newer snapshot is observed,
**Açıklama Kayıtlarını Yenile** refreshes the summary and then the result once.
Follow-up becomes available when both agree at the current revision.

An in-progress read pauses the actions while the editor holds edits in memory.
Save rechecks the scoped reads immediately before dispatch. Switching workflows,
closing the result view, leaving the page, or reloading discards unsaved
answers and edits. Questions and answers are plain text. Unsaved contents stay
out of URLs, browser history state, browser storage, query and mutation cache
data, logs, and telemetry. The mutation retains only the new record ID on
success. Saved records use the existing detail and history reads.

Save also compares its committed view with the router's live history entry.
The live check closes the interval between navigation and the old editor's
unmount. Back/Forward and leaving the document invalidate the previous binding.

## Save behavior

Only Save sends the existing `POST /api/v1/prompt-requests` request. The
original request, workflow, evaluation history and terminal questions remain
available under their original IDs. The follow-up creates no workflow, lineage
record, or provider operation.

Follow-up Save uses a relative same-origin endpoint, ignores `VITE_API_BASE_URL`,
and asks the browser to reject redirects before following them. The ordinary
New Prompt form still uses the legacy configurable transport and its redirect
behavior. Its broader transport policy is outside this change.

A synchronous lock prevents concurrent submissions from the form. The POST
has no automatic retry. A transport failure can occur after the server has
accepted the request. Check **Prompt Geçmişi** before trying again if the save
outcome is unclear. Leaving the view or losing verification suppresses
navigation from the old response. A POST already dispatched can still finish.

## Verification

Vitest exercises exact composition, the review and save boundary, validation,
trust withdrawal, workflow switches and private data handling. Chromium uses
real FastAPI, disposable SQLite and scripted fake providers. It compares the
original request and workflow reads before and after Save, verifies the new
request has no workflow, and covers keyboard review and unsaved reload on
mobile. The tests use synthetic data. See the
[workflow results guide](frontend-workflow-results.md) for the underlying
result and history views.

The remediation regressions cover a newer history snapshot surviving cache
eviction, same-turn workflow selection and Save, exact mixed line endings in
the submitted JSON, and zero requests at a CORS-permissive second origin for
301, 302, 303, 307 and 308 responses.
