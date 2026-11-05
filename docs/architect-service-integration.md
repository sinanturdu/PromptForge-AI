# Architect integration boundary — M5B-4

`app/modules/prompts/architect_integration.py` adds a database-free function rather
than a service class. `create_architect_handoff()` provides meaningful adaptation:
freeze an existing request DTO, bind caller-supplied identities and parent links,
convert it to the unchanged Architect input, call the agent once, and validate the
result for downstream consumption. No session, repository, run/version record,
ID allocation, retries, scoring or workflow state machine is introduced.

```python
from app.modules.prompts.architect_integration import (
    CandidateReady,
    create_architect_handoff,
    make_critic_input,
)

# saved_request is a trusted PromptRequestRead loaded by the existing service.
# agent is the existing PromptArchitectAgent with an injected LLMGateway.
handoff = await create_architect_handoff(agent, saved_request, request_id=correlation_id)
if isinstance(handoff, CandidateReady):
    critic_input = make_critic_input(handoff)  # data only; does not execute a Critic
else:
    questions = handoff.questions             # no candidate/version exists here
```

The helper is opt-in and is not called by `POST /api/v1/prompt-requests`. That route
still only saves the request using the existing service/repository transaction.
`PromptArchitectAgent.create()`, `ArchitectResult`, instructions, gateway policy,
ORM models and migrations remain unchanged.

## Identity and input snapshots

`ArchitectRequestSnapshot` derives from the existing `PromptRequestRead` schema,
freezes its primitive fields, hides sensitive content in ordinary representations,
and reuses `PromptRequestCreate` validation. The source ID, creation timestamp,
raw prompt, context and preferences are copied before awaiting the agent. An
external mutation of the original DTO cannot relabel a running result.

Without a run context, the handoff carries only the existing prompt-request ID.
It does not allocate a workflow, agent-run or candidate/version ID. To carry
already-existing run references, the future caller may supply:

```python
context = ArchitectRunContext(
    prompt_request_id=saved_request.id,
    workflow_run_id=existing_workflow.id,
    workflow_prompt_request_id=existing_workflow.prompt_request_id,
    agent_run_id=existing_agent_run.id,
    agent_workflow_run_id=existing_agent_run.workflow_run_id,
)
```

Local validation rejects cross-request workflows, cross-workflow agent runs and
orphan/incomplete references before any agent call. Parent IDs must come from the
loaded records, not be inferred from desired ownership. This validates consistency
of supplied assertions; it **does not query the database or prove existence,
authorization, current lifecycle state or protection against a stale/deleted row**.
The future persistence layer must verify those conditions transactionally.
Ownership here means request/run ancestry: the current local unauthenticated schema
has no user-owner column, and M5B-4 does not invent one.

## Explicit downstream outcomes

| Architect decision | Handoff | Meaning |
| --- | --- | --- |
| preserve | `CandidateReady` | Canonical task and rendered text equal the original exactly; no changes. Eligible for later Critic evaluation. |
| rewrite | `CandidateReady` | Non-null validated canonical content and its exact deterministic rendered text; changes kept separately. Eligible for later Critic evaluation. |
| clarify | `ClarificationRequired` | One to three bounded questions, no candidate/rendered/version fields. Resolve input before further generation/evaluation. |

`candidate_ready` means a valid **in-memory candidate**, not a persisted prompt
version, completed workflow, quality approval or final-version selection. No
`version_number`, evaluation round, score or fabricated record ID is returned.
Clarification never silently falls back to the raw prompt as a candidate.

The boundary revalidates returned models (including nested canonical/metadata data)
even when a caller used Pydantic `model_copy()` or bypassed validation. It checks
the target against the original request, current renderer name/version and exact
canonical/rendered consistency. Unsupported renderer identities fail explicitly;
add their validation only when those renderers are actually implemented. Existing
`ArchitectOutput` validators enforce decision and note rules without duplicating
the generation contract. Unknown usage remains unknown.

Invalid request/reference bindings raise the existing fixed `LLMError` category
`INVALID_REQUEST`; inconsistent Architect results use `INVALID_STRUCTURED_OUTPUT`.
Local validation errors retain neither private inputs nor exception context. Actual
Architect/gateway failures and external cancellation propagate unchanged: no fallback
candidate, extra agent operation or resource closing is performed. The caller
continues owning the gateway/provider lifespan.

## Future blind Critic input and evidence seam

`make_critic_input(CandidateReady)` revalidates the candidate and returns only:

- Original request snapshot, including user context and preferences.
- The exact rendered prompt to be evaluated.
- Target mode and renderer name/version.

It excludes Architect changes, questions, canonical/internal representation, execution
metadata, agent versions and any prior score. This follows ADR-010/ADR-011: evaluate
the exact delivered text against the original request, without optimizer commentary.
A clarification handoff or a tampered candidate is rejected. No Critic, score,
evaluation record or provider call is implemented by this projection.

For later REA work, add curated textual evidence at the agent-input assembly seam
while preserving the unchanged source request and provenance separately. Do not
overwrite the original request/context to disguise derived evidence as user intent.
No REA placeholder, report schema, fetching, image processing or multimodal change
is added now. How future Critic input receives separately identified evidence remains
a later contract decision; it must not be smuggled through Architect notes.

## Intended persistence mapping (documentation only)

| Existing model/field | Intended source and responsibility |
| --- | --- |
| `PromptRequest` | Existing request ID/content/preferences; no update during Architect generation. |
| `PromptVersion.prompt_request_id` | Original request ID, verified again before writing. |
| `PromptVersion.workflow_run_id`, `agent_run_id` | Existing validated run references when a future workflow owns the operation. |
| `PromptVersion.canonical_prompt` | Candidate canonical data serialized with `model_dump(mode="json")`; never notes or telemetry. |
| `PromptVersion.rendered_prompt`, `target_mode`, renderer fields | Exact candidate text, original target, validated renderer identity. |
| `PromptVersion.source` | Existing `engineer_create` for this initial-create operation, for both preserve and rewrite. `engineer_revise` is reserved for future revision behavior. |
| `PromptVersion.change_summary` | Candidate's user-facing changes; empty for preserve. No explanation is appended to rendered text. |
| `PromptVersion.analysis` | Only explicitly chosen analysis/version metadata, if needed; no fabricated Critic approval or hidden reasoning. |
| `AgentRun` identity/operation fields | Caller-created existing run ID; Architect name, agent/instruction versions and `operation="create"`. |
| `AgentRun` telemetry fields | Gateway attempts/repair flag, known input/output counts and duration with an explicit float-to-integer rounding policy. Unknown counts stay NULL. |
| `AgentRun.parameters` | Allowlisted model parameters/policy and completeness flags, if retained; never a full Settings dump, credentials or prompt payload. |
| `WorkflowRun` | The orchestrator owns transitions, round counters and lifecycle. Candidate generation alone must not populate quality scores, qualification or `final_version_id`. |

Clarification produces **no PromptVersion** and no Critic evaluation. If a future
caller has already created an AgentRun, the Architect operation may have succeeded
by asking questions; that is different from completing the workflow. Current
`WorkflowRunStatus` has no waiting-for-input state. A durable clarification/resume
policy and question storage require an explicit future decision (possibly a
migration); do not label clarification as a completed candidate or silently reuse
GENERATING/EVALUATING as a waiting state.

## Required future transaction boundaries

1. Use a short transaction to load the request and verify run ancestry, allowed
   state and any idempotency claim. If future execution records are needed, create
   them explicitly there. Snapshot and close the session before the LLM wait.
2. Run Architect outside the database transaction. Never keep an AsyncSession
   transaction/SQLite write lock open across generation, retries, repair or user input.
3. In a fresh short transaction, recheck ownership/state and atomically store the
   candidate (if any), operation telemetry and appropriate workflow transition.
   The existing repository stages/flushes; the application service explicitly
   commits or rolls back. `DatabaseSessionManager.session()` rolls back on errors
   and closes, but does not auto-commit. No such writes occur in this milestone.
4. Handle failure/cancellation in a separate deliberate transaction if execution
   records were previously created. Persist only safe error categories/messages,
   never raw exceptions. Startup recovery for interrupted background runs remains
   the planned ADR-005 responsibility, not an existing workflow implementation.

Unresolved before enabling writes:

- Concurrency-safe per-request `version_number` allocation; the existing unique
  `(prompt_request_id, version_number)` constraint is a guard, not an allocator.
- Retry/idempotency rules around the unique `PromptVersion.agent_run_id`; a repeated
  completion must not create a second version. Gateway attempts/JSON repair are
  one logical AgentRun, not separate evaluation rounds or versions.
- Cross-request relationships are not fully guaranteed by individual foreign keys;
  parent versions and final-version ownership also need transactional checks.
- Lifecycle/clarification resumption, user-edited request snapshots and stale-task
  handling; completed-round/final-selection decisions require the future Critic.
- Per-run version/evaluation caps need orchestration logic in addition to current
  numeric range checks (ADR-003); no counters advance in M5B-4.
- `CallMetadata` permits identifiers longer than current AgentRun provider/model
  columns (64/128); reject or deliberately evolve the schema, never truncate silently.
- Current columns cannot fully represent partial usage, cached/reasoning counts,
  upstream IDs or all error metadata. Choose allowlisted JSON storage or a later
  migration. Observed partial totals must not be presented as complete billing data.
- Execution start/end UTC timestamps must come from the future executor. Monotonic
  gateway duration alone cannot reconstruct them; safe version/renderer limits and
  canonical schema-version storage need an explicit persistence policy.

## Verification and privacy

`tests/test_architect_integration.py` uses the existing fake provider and offline
pytest/AnyIO conventions. It covers all outcomes, original-text preservation,
ancestry checks, malformed/tampered results, blind-input eligibility, frozen snapshots,
no SQL activity, error propagation and sensitive-content exclusion. The existing
backend suite retains development SQLite/WAL/SHM snapshot guards.

The helper emits no logs. Sensitive source, candidate, questions, changes and
execution fields are excluded from ordinary handoff representations. Explicit
model dumps, Critic inputs and structured validation details still contain private
content; they are not diagnostic payloads. These checks establish integration
contracts, not semantic prompt quality or verified database existence.

M5C-4 adds the opt-in [Architect -> Critic single pass](architect-critic-pass.md).
It reuses this handoff and clarification contract, binds the exact Critic input and
returns a validated evaluated candidate with independent scoring. This helper's
existing API, transaction responsibilities and absence of persistence are unchanged.
