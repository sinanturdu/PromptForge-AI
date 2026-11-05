# Prompt Architect benchmark — M5B-3

This is a development evaluation foundation, not a production score or independent
Prompt Critic. Software contracts and semantic prompt quality are reported separately.
There is no length target, expansion ratio, mandatory prompt template or aggregate
quality grade. A longer prompt can help or hurt; judge the resulting task outcome.

## Dataset

The shared source is `tests/fixtures/architect_cases.json`, dataset version **1.0.0**.
It contains 36 cases: translation, creative, coding, analytical, editing and reference
tasks, six per category and three Turkish/three English per category. Low/Medium/High
each appear in 12 cases. Eight cases contain genuinely blocking ambiguity. Other
cases include protected code/quotes/layout, explicit negative constraints, optional
details, supporting-context injection and future reference-oriented work.

Each case records its ID, category, language/tags, existing `PromptRequestCreate`
payload, acceptable decision set, requirements, forbidden additions, clarification
necessity and rationale. Requirements describe what the receiving model should be
asked to do; the Architect must not execute that underlying task itself. Equivalent
wording is acceptable, and some already-actionable requests permit preserve or a
useful rewrite. There is no single required rewritten answer.

`required_literals` are narrowly scoped to exact source identities: code, protected
quotes, identifiers, URLs or explicitly protected format. They must exist in the
input. Do not use ordinary prose requirements as exact-match rules; paraphrases,
numeric representations and semantic equivalence need human review. Literal presence
alone does not establish preservation of its meaning or scope. `forbidden_literals`
contain selected suspect requirements and only trigger review, never automatic guilt.

The original nine examples remain under `contract_example` and drive the existing
M5B-2 tests. Those authored outputs/fragments check deterministic software behavior;
they are not unique gold answers, and the benchmark runner ignores them entirely.
`architect_cases.py` validates IDs, uniqueness, input schemas, reference grounding,
decision/clarification consistency and complete review notes. Review expectation
changes and bump the dataset version when changing evaluation semantics.

## Run offline

From `backend/`:

```powershell
uv run python -m benchmarks.prompt_architect
uv run python -m benchmarks.prompt_architect --report
```

The runner injects **FakeLLMProvider**, regardless of inherited provider settings or
credentials. Every case receives the same preserve-only script, independent of its
reference labels. It exercises the actual `PromptArchitectAgent.create()` interface
and existing gateway with retries/repair disabled. Providers are closed after each
case. No SDK configuration, API key, database or network request is needed.

The fixed baseline is deliberately poor for rewrite/clarify cases. Its reference
alignment failures are expected diagnostics, not pytest failures or measurements of
a real model. Exit code zero means the harness ran; it never means prompt quality
passed. Errors reading/validating the dataset produce a nonzero exit code with a
fixed message rather than private validation content.

The optional content-free JSON report is written only to
`backend/benchmark-reports/prompt-architect-offline.json`, ignored by Git. It records
dataset/harness/agent/instruction/renderer versions and a SHA-256 of the normalized
validated dataset. Runs contain no timestamps or invented usage/latency measurements
and produce reproducible reports. Console output is only a count summary. Neither
console output nor report contains input prompts, candidate text, raw errors or
credentials. The CLI suppresses gateway diagnostics while running, then restores
the prior logger state. No arbitrary content-export path is accepted.

## Findings and review workflow

Objective findings cover:

- Result schema and required execution/version metadata.
- Decision output boundaries, target preservation and deterministic rendering.
- Exact original-text preservation when the candidate claims `preserve`.
- Alignment with the human-authored acceptable decision set.
- Missing explicitly protected literals, reported by rule index.
- Suspect forbidden-literal occurrences, flagged for contextual review by rule index.

`pass` applies only to that narrow check. Checks with no literal rules or no candidate
text are `not_applicable`. A forbidden phrase may be quoted, negated or discussed;
the finding is `review`, not a semantic failure. A changed preserve output is an
objective contract failure even if the change seems harmless. Decision alignment is
an observable comparison with reference labels, not proof that a decision was wise.

Every case retains all eight semantic criteria below as **pending**, and every
report says `quality_verdict: not_assessed`. Even an unrelated but well-formed
rewrite can pass all available literal checks; a regression test demonstrates this.
No fake response can establish actual usefulness or downstream success.

To inspect a case, find its `case_id` in the shared dataset, read its rationale,
requirements and forbidden additions, and review the candidate against the rubric.
For this baseline the candidate is exactly the original `payload.raw_prompt`.
Keep reviewer notes separate from generated machine reports and record evidence
for each judgment. Do not convert absent objective findings into an automatic pass.

## Human quality rubric

Assess each criterion as satisfactory, minor issue, major issue, critical issue, or
unassessable, with a short evidence-based note. No numeric aggregation is defined.

| Criterion | Observable failure examples |
| --- | --- |
| Intent fidelity | A story request becomes a deployment task; the Architect answers the task instead of preparing its prompt. |
| Explicit requirement preservation | A protected code block changes, Python 3.12 becomes another version, or an offline-only constraint disappears. |
| Outcome clarity | A comparison never asks for the requested recommendation, or the actual deliverable becomes unclear. |
| Relevant context and sufficient specificity | Necessary supplied source material is omitted, or injected context instructions become authoritative. |
| No invented requirements | Unrequested deadlines, budgets, technologies, page observations or user preferences are asserted as requirements/facts. |
| Appropriate complexity | A simple translation acquires an expert persona, repeated rules and a mandatory reasoning checklist; useful detail is removed merely to shorten text. |
| Decision suitability | A missing source text is invented rather than requested; optional audience details block an actionable task; a good prompt is expanded without benefit. |
| Task suitability | Creative freedom is replaced by a compulsory plot, a coding patch expands to a refactor, or analysis uses invented decision criteria. |

Severity guidance: **critical** violates a hard constraint, changes essential intent,
or invents consequential facts; **major** materially reduces usefulness or blocks
the intended outcome; **minor** introduces avoidable friction without losing core
requirements. Disputed or insufficiently evidenced judgments remain unassessable.
Machine-finding severity is triage for the explicit rule, not a production Critic
verdict. Review labels, rationale and any disagreement before drawing conclusions.

## Later, separately authorized real-model evaluation

Keep any live runner separate from this fake-only CLI. A future explicit opt-in
runner can reuse M5A composition and the Architect interface, record candidates,
then call the pure `evaluate_candidate(case, candidate_json)` function. It must not
replace the offline CI path or automatically enable API use from environment values.

For each case compare the original and optimized prompts with the same downstream
model/settings and task inputs. Use blind or counterbalanced presentation and repeat
where sampling variability matters. Evaluate intent/constraints first, then actual
downstream task success, unwanted restrictions and usefulness. Use task-specific
checks where available and human review for semantic equivalence and creative work.
Do not make the Architect self-certify its output or claim independent Critic approval.

Record dataset, instruction, renderer and exact model versions/settings. Keep
Architect-generation usage/latency distinct from downstream execution measurements;
unknown/partial usage stays unknown/partial. Measure observed costs only in a later
authorized experiment; fake usage and controlled-clock durations are not savings
estimates. Store authorized content separately from content-free diagnostic reports,
with synthetic/public inputs preferred. No live runner, Critic, REA, fetching or
multimodal functionality is implemented in M5B-3.

Verification: `uv run pytest tests/test_architect_benchmark.py`, plus the full backend
suite, Ruff and `uv run mypy app tests benchmarks` (also used by CI). The three
reported instruction-text typos were already absent; instruction version **1.0.0**
and production Architect/gateway contracts remain unchanged.
