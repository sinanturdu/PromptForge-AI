# Independent Prompt Critic — M5C-2

## Current boundary

The existing Architect and blind-input contracts remain unchanged. The optional
in-memory path is:

```python
from app.agents.prompt_critic import PromptCriticAgent
from app.llm.schemas import ModelSettings
from app.modules.prompts.architect_integration import CandidateReady, make_critic_input
from app.scoring.engine import ScoringEngine

if isinstance(handoff, CandidateReady):
    critic_input = make_critic_input(handoff)
    critic = PromptCriticAgent(gateway, ModelSettings(model="explicit-model-id"))
    result = await critic.evaluate(critic_input, request_id=correlation_id)
    scoring = ScoringEngine.score(result)
else:
    questions = handoff.questions  # no candidate/version or Critic evaluation
```

Preserve and rewrite candidates are eligible, including strong unchanged Turkish
or English prompts. No Architect decision, notes, canonical representation, run
context, prior scores or telemetry reaches the Critic. Request IDs and timestamps
remain local; the provider receives relevant original content/preferences, exact
rendered text and supported target/renderer context. Target family is not the
internal execution model/provider.

The agent snapshots and deeply revalidates input/settings before provider access,
checking target consistency, nonblank bounded candidate text and the currently
supported GenericPromptRenderer 1.0.0. Only `CriticInput` is accepted, so clarification
handoffs fail before a gateway call. `make_critic_input()` remains the supported
factory and checks canonical/rendered agreement on its CandidateReady argument.

Revalidation is not authentication: CriticInput deliberately omits canonical data
and cannot prove that an otherwise well-formed text was created by the Architect.
Trusted callers must use the factory and preserve identity/state at future writes.
No contract change or opaque authenticity mechanism is introduced here.

`evaluate()` calls `LLMGateway.generate_json(request, CriticOutput)` exactly once.
M5A owns bounded transport retries and JSON/schema repair. There are no agent-level
retries, quality rounds, rewriting or score arithmetic. Gateway errors and external
cancellation propagate without fallback; provider/gateway lifetime stays with the
caller. There are no sessions, queries, records or API hooks.

## Contracts and privacy

All models reuse frozen, strict, extra-forbidden `LLMModel`. The provider schema
contains required keys only; nullable fields remain required and unused arrays are
empty. Bounds and cross-field checks are local validators, preserving compatibility
with the existing strict structured-output preflight. No schema keywords are dropped.

- `EvidenceRef`: permitted source and exact quote.
- `CriterionAssessment`: integer 0-10 or null, concise rationale and evidence.
- `CriterionScores`: nine fixed required criterion fields, no arbitrary dictionary.
- `CriticIssue`: one primary criterion, code, supported/uncertain status, nullable
  severity, specific description, actionable recommendation and evidence.
- `CriticOutput`: assessments, issues and summary; no model total/approval/versions.
- `CriticResult`: normalized validated output, evaluated target/renderer, local
  input fingerprint, agent/instruction/rubric versions and separate CallMetadata.
- `ScoringResult`: Decimal total or null, qualification status, passed flag,
  explicit gates and rubric/scoring versions. Its own invariants reject inconsistent
  statuses, totals and gates.

Rationales, quotations and issue descriptions/recommendations are nonblank and at
most 600 characters. Evidence arrays contain at most three unique references;
assessed criteria and supported issues need at least one. At most twelve issues
and an 800-character summary are allowed. Bounds are resource caps, not length goals.
Null assessments can have no evidence when the necessary information is unavailable.

Content, evidence, explanations and fingerprint are hidden in normal representations.
The agent emits no logs; gateway diagnostics remain allowlisted. Dumps, structured
validation details and content returned to the caller remain sensitive. Safe errors
have fixed categories/messages without private validation exception context.

The fingerprint is SHA-256 of sorted-key, UTF-8 JSON for the full local CriticInput
snapshot (including existing identity, exact text and renderer). It binds a result
to that snapshot for trusted future callers; it is not authentication, a safe log
identifier or proof of semantic correctness. Changing content, target or renderer
requires a new evaluation. Execution usage remains separate and unknown usage stays
unknown; no duration/usage claims are inferred from fake tests.

## Evidence and issue taxonomy

Permitted sources: `raw_prompt`, `user_context`, `output_language`, `task_profile`,
`prompt_detail_level`, `response_detail_preference`, `reasoning_preference`,
`target_mode`, and `rendered_prompt`. Preference enums are matched against their
string values. A quote must occur exactly, case-sensitively in the declared field;
null source fields cannot support a quote. No arbitrary paths, URLs or external
resources are resolved. All assessment and issue references are checked before
normalizing uncertainty, even if the score will become null.

Missing requirements can quote the original requirement without pretending to quote
an absence in the candidate. A substring match verifies attribution only; negation,
quotation context, relevance and semantic correctness still require judgment.
Fabricated/misattributed evidence produces `INVALID_STRUCTURED_OUTPUT` locally,
without another model call. Structural contradictions remain ordinary gateway
schema-validation failures, subject only to its existing bounded repair policy.

| Code | Intended finding |
| --- | --- |
| intent_shift | Requested task or desired outcome changed. |
| missing_requirement | Necessary supplied requirement/input omitted or altered. |
| invented_requirement | Unrequested restriction, specification or preference imposed. |
| contradiction | Instructions conflict internally or with the source. |
| unsupported_context | Context asserted without supplied support. |
| unnecessary_complexity | Irrelevant persona, repetition or procedure adds friction. |
| insufficient_specificity | Necessary supplied detail is too vague to use. |
| task_mismatch | Approach does not suit the actual creative/technical/analytical task. |
| format_mismatch | Requested output format is violated or another is imposed. |

Severity definitions, anchors and exact weights/gates are in
[ADR-010](adr/ADR-010-independent-critic-and-deterministic-scoring.md).
Supported findings need severity/evidence. Uncertain findings cannot assert severity;
they make the primary criterion null. Optional speculative improvements are not issues.
Unsupported deductions below 9 become null with a fixed local rationale; scores
at most 6 need major/critical support. This removes certainty rather than inventing
findings. No LLM operation is added. Other evidence is retained and input is unmodified.

Exact duplicate findings are rejected, including assignment to another primary
criterion. One concern must not mechanically multiply across overlapping criteria;
detecting differently worded semantic duplicates remains a model/human limitation.

## Production scoring versus benchmark review

Production rubric **2.0.0**, Critic agent/instruction **1.0.0** and scoring **1.0.0**
are independent versioned policies. Python uses an independent Decimal context to
sum `score * weight / 10`, without rounding before gates. Qualification requires
all nine scores, total >=85, zero supported critical issues and intent >=7. Any null
score means `unassessable`, total null, passed false and no weight renormalization.
Otherwise status is `qualified` or `not_qualified`; no synthetic grade is assigned.

The M5B dataset remains 36 cases with independent pending human reviews and no
aggregate human score. The correspondence is interpretive, not a score conversion:

| Human-review criterion | Production relationship |
| --- | --- |
| Intent fidelity | intent_fidelity |
| Explicit requirement preservation | completeness, constraints_and_scope |
| Outcome clarity | clarity, output_format |
| Relevant context and sufficient specificity | context_and_role, specificity |
| No invented requirements | constraints_and_scope, context_and_role |
| Appropriate complexity | structure, specificity, clarity |
| Decision suitability | Remains broader human review: clarification/Architect decisions are hidden from the Critic. |
| Task suitability | intent_fidelity, specificity |

`qualified` means reported judgments pass defined gates. Neither valid JSON nor
grounded quotations guarantee a correct interpretation, useful downstream output
or calibrated agreement with humans. Separate instructions/settings provide blind
evaluation, not statistically independent errors. A future separately authorized
real-model/human evaluation must measure false approval, false rejection, useful
brevity, language/task coverage and score inflation. Fake tests cannot do this.

## Future integration

No API, evaluation table, migration, workflow or frontend feature is activated.
The existing request-creation API still only persists requests. Later evaluation
persistence must bind exact version/target/renderer/rubric and represent null scores,
uncertainty, structured evidence, fingerprint and telemetry explicitly. The planned
tables are not current implementations; do not force these contracts into existing
PromptVersion.analysis as a substitute for an evaluation record.

Reuse [M5B-4's transaction boundaries](architect-service-integration.md): short
load/ancestry/state transaction, no session across LLM waits, then fresh transaction
to revalidate ownership/idempotency and atomically write execution/evaluation/state.
No IDs or records are invented by either agent or scorer. Future rounds reuse the
same rubric snapshot; retries/repair are not evaluation rounds. Supported actionable
findings can guide revision; each new Critic call remains blind to previous scores
and Architect notes. Three-round orchestration and best-version selection remain
ADR-003/ADR-007 work, not agent behavior.

Input assembly leaves an evidence/provenance seam for a future separately designed
REA adapter. Preserve unchanged user intent separately from derived findings and
their source/trust level. No placeholder fields, screenshots, URLs, multimodal
changes or REA implementation are introduced.

## Offline verification

`uv run pytest tests/test_prompt_critic.py` uses FakeLLMProvider, recording doubles,
synthetic English/Turkish examples, protected code/version/constraint cases and pure
scoring tests. These scripted judgments verify contracts, privacy, evidence
attribution, arithmetic, gates and lifecycle behavior, not actual model judgment.
The full backend suite retains external-connection blocking and per-test development
SQLite/WAL/SHM snapshots. No live model or dependency additions are required.

M5C-3 extends the same Critic fixture to 36 bilingual scenarios with 43 scripted
software probes. `uv run python -m benchmarks.prompt_critic --report` exercises
the existing agent/scorer and keeps all semantic verdicts pending. See the
[adversarial benchmark and controlled-calibration procedure](critic-benchmark.md).
