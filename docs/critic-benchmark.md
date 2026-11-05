# Adversarial Critic benchmark — M5C-3

This is an offline software-evaluation foundation, not a measurement of model
accuracy. Valid contracts, grounded quotation locations and correct arithmetic
do not establish that an interpretation, severity or quality verdict is right.
Outcome fidelity, useful brevity and unnecessary restrictions matter; length,
personas and templates are never quality targets.

## Dataset

`backend/tests/fixtures/critic_cases.json` is the shared versioned source, currently
**1.0.0**, loaded through `benchmarks/critic_cases.py`. It extends the thirteen
M5C-2 regression examples without removing their stable IDs or authored scripts.
The M5B Architect dataset is unchanged.

There are **36 scenarios**, eighteen Turkish/eighteen English. Each of coding,
creative writing, translation, editing, analytical comparison and reference/
information requests has six cases, three per language. Primary categories are:

| Category | Cases |
| --- | ---: |
| Strong prompts / false-rejection concerns | 7 |
| Intent and requirement preservation | 7 |
| Invented requirements / over-prescription | 10 |
| Injection / evaluation manipulation | 3 |
| Evidence and scoring integrity | 9 |

Categories overlap conceptually: evidence controls also use strong or defective
candidates. Each scenario has a stable ID, language/task/category, existing request
payload/preferences, candidate, intended behavior, protected source constraints,
human review expectation/considerations and rationale. Protected references must
occur in supplied raw/context text; they are human-review anchors, not exact-match
requirements for all candidate prose. Equivalent critiques and paraphrases remain
acceptable; there is no unique gold natural-language critique.

`review_expectation` is a curated hypothesis (`strong`, `defective`, `disputed`),
not a completed human/model review. Per-case `probes` define one or more explicit
software scripts and independent expected error/status/total. Thirty-six scenarios
currently contain **43 probes**. The original thirteen authored controls are kept
under `contract_example` and still drive the existing M5C-2 tests.

Version the dataset when changing scenario/reference/probe semantics. Keep IDs
stable for unchanged cases, document changes and review language/task balance.
Changing a human label does not change the fake response. Expected totals/outcomes
are independent test oracles, not instructions sent to the Critic.

## Run and interpret

From `backend/`:

```text
uv run python -m benchmarks.prompt_critic
uv run python -m benchmarks.prompt_critic --report
uv run pytest tests/test_critic_benchmark.py tests/test_prompt_critic.py
```

The runner uses only FakeLLMProvider and recording doubles. It never loads provider
configuration, selects a live provider or reads credentials. Inherited OpenAI
environment values cannot enable real calls. It creates synthetic request DTOs,
not database records, and exercises actual PromptCriticAgent/ScoringEngine APIs.
No gateway/agent quality loop is added. Probe gateways disable transport retries
and JSON repair so one generated diagnostic consumes one operation/one fake call;
invalid input consumes zero. Existing tests separately verify gateway-managed retry/
repair remains one agent operation. Every provider is closed in `finally`.

Scripts include perfect scores, grounded authored issues, fabricated/misattributed/
missing evidence, contradictory severity, uncertainty/null scores, unsupported
deductions, exact thresholds and invalid input. The default perfect-score script
deliberately approves some defective candidates. Other scripts reject sufficient
prompts for lacking personas or misinterpret a negated quotation. These controls
make possible false approval/rejection visible without claiming a model made them.

Reports distinguish:

- Objective checks: expected rejection/status/exact total, independent Decimal/gate
  oracle, operation/call budget, unchanged input, blind message content and cleanup.
  Database/network exclusion is additionally enforced by regression tests, not
  inferred from an absence of reported calls.
- Expected diagnostic rejections: malformed contracts, attribution defects and
  invalid inputs. A rejection is a successful software control when expected.
- Review flags: comparisons between scripted qualification and human-authored
  reference expectations. These are review pointers, never measured false-positive
  rates or semantic failures. Disputed judgments always need contextual review.
- Semantic review: all eight shared benchmark human-review criteria remain pending,
  and every case/report retains `quality_verdict: not_assessed`.

Production rubric scores are explicitly named `scripted_total`/`scripted_status`.
They are software-control outputs, not benchmark human verdicts or model quality
measurements. Literal evidence checks verify attribution only. The negated Kubernetes
case intentionally passes every objective check while its claim remains questionable.

The default run currently reports 36 cases, 43 probes, five expected diagnostic
rejections, zero objective failures and twenty semantic-review flags. None of those
counts measures Critic accuracy or downstream task success.

Exit codes: **0** means all scripted objective expectations held, with semantic
review still pending; **2** means an objective regression (also argparse usage
errors); **1** means dataset/runtime/provider/report infrastructure failure.
Unexpected provider errors are not disguised as expected diagnostic rejections.
Errors and the console summary contain no raw content or traceback.

## Reproducibility, privacy and shared tooling

Reports preserve dataset order and contain dataset/hash, rubric, instruction,
agent, scoring, renderer and harness versions. There are no timestamps, fabricated
usage/cost/latency measurements, prompts, candidates, evidence or credentials.
Real-model usage and latency are explicitly null. Case IDs/probe names are public
catalog identifiers, not user content. Explicit dataset/agent dumps remain private.

`--report` writes only `backend/benchmark-reports/prompt-critic-offline.json`, already
ignored by Git. No output-path option exists. Architect and Critic runners share
the small `benchmarks/reporting.py` writer; this is not another benchmark framework.
It rejects redirected directories/files and unsupported names, writes atomically,
protects existing hardlink source files and cleans temporary files after failures.
Repeated runs produce byte-identical report contents. Existing Architect report
semantics are unchanged.

## Later human review and separately authorized calibration

Do not mark reviews complete merely because probes passed. For each case, give
independent reviewers original request/preferences and exact candidate, blind to
Architect commentary, Critic scores and generated conclusions initially. Record
evidence-based assessment of intent, explicit requirements, outcome clarity,
context/specificity, invented requirements, complexity, decision suitability and
task suitability. Then inspect Critic issues/rationales for relevant evidence,
correct interpretation, severity and actionable feedback. Keep disagreements or
insufficient information explicitly unassessable; adjudicate instead of treating
the Critic as its own ground truth. Preserve reviewer notes separately from the
content-free machine report, with dataset/rubric/candidate identity.

A later opt-in real-model experiment must be separately authorized and cannot be
enabled by this CLI. Use synthetic/public or explicitly authorized inputs; fix
dataset, instruction/rubric/renderer, exact models and settings. Evaluate paired
original/candidate prompts with counterbalanced presentation and independent human
review, separating Architect/Critic generation from downstream task execution.
Include unchanged strong prompts, flawed candidates, paraphrases, quoted injections
and adversarial evidence. Measure false approval of constraint/intent damage and
false rejection of useful brevity against adjudicated human labels, not the model's
own score. Stratify by task/language, repeat where sampling varies, report reviewer
disagreement and unknowns, and avoid unsupported aggregate accuracy claims.

Keep production qualification separate from human usefulness and downstream success.
Record observed costs/usage separately, impose explicit budgets and retention rules,
and do not publish private prompts or treat unknown counts as zero. No live runner,
model calls, orchestration, persistence, API, frontend or REA changes are part of M5C-3.
