# ADR-010: Bağımsız Prompt Critic ve Deterministik Puanlama Motoru

- **Durum:** Onaylandı (M5C-2: in-memory Critic ve deterministik puanlama uygulandı; değerlendirme kalıcılığı ve workflow planlandı)

## Bağlam

Bir ajanın kendi ürettiği promptu değerlendirmesi veya toplam kalite puanının doğrudan LLM aritmetiğine bırakılması yanlı ve tutarsız sonuçlar doğurur.

## Karar

`PromptCriticAgent` evaluates the original request against the exact rendered candidate
using the existing blind `CriticInput`. It sees no Architect explanations, decision
labels, canonical representation, execution telemetry, evaluation history or prior
scores. The production role formerly called `PromptEngineerAgent` is implemented
as `PromptArchitectAgent`; it does not approve its own work.

### Production rubric 2.0.0

M5C-2 supersedes the original nine-criterion rubric v1 weights. The criterion keys
remain stable; definitions and weights now emphasize outcomes and constraints.

| Criterion | Weight |
| --- | ---: |
| intent_fidelity | 25 |
| clarity | 10 |
| completeness | 15 |
| structure | 5 |
| context_and_role | 5 |
| constraints_and_scope | 20 |
| output_format | 5 |
| specificity | 10 |
| consistency | 5 |

Intent, requirement coverage and constraints account for 60%. Roles, organization
and format are judged conditionally: absent headings, personas, examples or XML
are not defects. Useful brevity and an unchanged strong original can earn full
marks. Low/Medium/High means useful specificity, not a minimum prompt length.
Definitions and immutable weights live in `app/scoring/rubric.py` and also supply
the Critic instruction; there is no model-provided weight or total.

`ScoringEngine` computes `sum(Decimal(score) * Decimal(weight) / Decimal(10))`
in an independent Decimal context. Gates apply before display rounding:

- total >= 85/100;
- no supported critical issues;
- intent_fidelity >= 7/10.

All nine scores must be assessable. Status is exclusively `qualified`,
`not_qualified` or `unassessable`. Any null criterion means no total, no
renormalization and `passed=False`, with unknown gates explicitly null.

Score anchors are 9-10 substantially satisfied, 7-8 minor defects, 4-6 material
defects and 0-3 severe failure. Supported minor issues cap their primary score at
8; major/critical issues cap it at 6. Contradictory score/severity combinations
are rejected. A deduction below 9 without a supported actionable finding becomes
unassessable locally; a score at most 6 also needs a major/critical finding.
No issue is fabricated and no additional semantic-repair call is made.

### Uncertainty and evidence

Supported issues require grounded evidence and severity. Uncertain issues have
null severity and make their primary criterion unassessable. Reporting an issue
means it affects the criterion; optional speculative suggestions should be omitted.
Critical means essential intent/hard constraints violated or consequential facts
invented; major materially reduces usefulness; minor adds friction without losing
core requirements. Each issue has one primary criterion. Exact duplicate findings
cannot be reassigned across criteria; semantic overlap still requires judgment.

Every supplied quotation is checked literally against an explicit permitted source
field, including quotations attached to uncertain findings. An omission can cite
the original requirement alone. Unknown external facts must not be presented as
verified observations. A quotation match proves attribution, not interpretation.
Invalid attribution fails locally with a fixed safe LLM error, without another
gateway operation. Raw content and validation details never become diagnostics.

Agent, instruction and scoring versions start at 1.0.0; rubric version is 2.0.0.
Changes to weights, definitions, anchors or gate semantics require rubric version
review; instruction and implementation changes have their own versions. Old/new
rubric scores are not interchangeable or silently compared across workflow rounds.

## Sonuçlar

- Arithmetic, gates and contract invariants are deterministic and unit-testable.
  Model judgments remain fallible: `qualified` means the reported evaluation passes
  these gates, not verified downstream task success or calibrated human agreement.
- M5B's eight human-review criteria retain their independent nonnumeric verdicts.
  Fake tests do not complete those reviews or establish semantic quality.
- No API activation, evaluation records/migrations, workflow loop, Critic rewriting,
  REA implementation or real-model calls are introduced.
- [Critic contracts, rubric mapping, limits and future integration](../prompt-critic.md).

