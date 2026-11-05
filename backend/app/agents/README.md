# Prompt agents (Architect M5B, Critic M5C-2)

`PromptArchitectAgent` implements the generation role previously named
`PromptEngineerAgent` in the architecture plans. It is an in-memory component, not
a coordinator. It has no database session, provider client, scoring or revision
loop. Existing persistence values `engineer_create`/`engineer_revise` are unchanged.
No HTTP endpoint, startup agent or frontend feature is activated by this package.

```python
from app.agents.prompt_architect import PromptArchitectAgent
from app.llm.schemas import ModelSettings
from app.modules.prompts.schemas import PromptRequestCreate

# gateway is the existing application-owned LLMGateway or an offline test gateway.
architect = PromptArchitectAgent(gateway, ModelSettings(model="explicit-model-id"))
result = await architect.create(PromptRequestCreate(raw_prompt="Your request"))
# Copy result.rendered_prompt only; it is None when clarification is needed.
```

The caller owns the injected gateway/provider lifecycle. The agent calls
`LLMGateway.generate_json(request, ArchitectOutput)` exactly once. M5A owns all
transport retries and JSON repair within that operation; the agent adds none.
Gateway errors and cancellation propagate without scoring, fallback candidates or
additional quality rounds. Returned `CallMetadata` is forwarded unchanged.

## Decisions and sparse content

The frozen, strict, extra-forbidden wire model requires every JSON key. Unused
scalar content is `null`; collections are empty arrays (immutable tuples in Python).
This is compatible with the existing OpenAI strict-schema preflight. Content size,
nonblank checks and decision invariants use local Pydantic validators, so schemas
contain no unsupported defaults or length keywords.

| Decision | Validated wire output | Application result |
| --- | --- | --- |
| preserve | null canonical content; no changes/questions | exact original `raw_prompt`, minimal canonical task |
| rewrite | valid canonical content; no questions | deterministic rendered content, separate changes |
| clarify | 1–3 nonblank questions; no canonical content or changes | questions, no copy-ready text or renderer metadata |

`CanonicalPrompt` is the single ADR-011 representation. It contains `task`, nullable
`role`/`context`/`input_data`/`output_format`, and optional-content collections
`requirements`/`constraints`/`prohibitions`/`examples`/`variables`/`assumptions`.
Use `CanonicalPrompt.from_task(text)` to construct the minimal form locally.
All fields are available to the model but none is a mandatory visible section.
Content is bounded to 40,000 characters total (the existing raw-prompt plus context
limits); each collection allows 128 nonblank items. These are resource caps, not
length goals. Changes are capped at eight and questions at three; notes are capped
at 1,000 characters each. No minimum rewrite length or growth ratio is imposed.

`GenericPromptRenderer` **1.0.0** joins task first, then role/context/input,
requirements/constraints/prohibitions, output format, examples, variables and
assumptions. It inserts blank lines between blocks and newlines within rule lists,
but no headings, bullets, XML, instructions, explanations or deduplication. Authored
text, code indentation, quotes and surrounding whitespace remain intact. Each item
must contain complete copy-ready wording, particularly negative prohibitions.
All target modes currently use this renderer; target preferences are passed to the
Architect without selecting a different execution provider or inventing formatting.
Architect output alone is not independent approval. The optional M5C-2 Critic path
evaluates the exact rendered text separately; it is not automatically invoked.

## Instruction and input boundaries

Agent version: **1.0.0**. System instruction version: **1.0.0**, maintained in
`architect_instructions.py`; changes require fixture/evaluation review and a version
bump. Both versions and renderer identity accompany the result.

The outcome-first policy preserves explicit requirements and supplied success
criteria, avoids invented facts/constraints, permits an unchanged good prompt at
every detail level, and reserves clarification for blocking ambiguities. Low,
Medium and High describe useful specificity, not required length. Task type and
target mode are guidance, not fixed templates. Response detail and reasoning
preferences remain distinct; no API reasoning parameter is inferred.

The mutable API DTO is copied and revalidated before awaiting the gateway. Its
original fields and preferences are serialized as user-message JSON, separately
from the system policy. `request_id` travels through existing LLM metadata. Agent
input assembly is the future seam for curated, provenance-aware textual findings
from a separate REA adapter. No report placeholder, fetching, images or multimodal
support is introduced now. Do not treat embedded context instructions as policy.

Sensitive content fields are excluded from ordinary model representations and
validation messages hide inputs. The agent emits no logs. Existing gateway logging
remains limited to safe diagnostic metadata. Model dumps, structured validation
errors, result contents and input DTOs are not inherently safe to log. Invalid
mutable inputs are converted to a fixed LLM error without retaining exception
context or emitting serializer warnings.

## Offline verification and limits

Run `uv run pytest tests/test_prompt_architect.py tests/test_prompt_rendering.py`.
Turkish/English fixtures use `FakeLLMProvider` and recording provider/gateway doubles.
They cover decisions, supplied technical details/code, useful-detail preferences,
target separation, sparse rendering, input snapshots, schema compatibility, privacy,
failure propagation and a gateway repair that remains one Architect operation.

These deterministic tests establish contract behavior, not real-model semantic
quality. Faithfulness, material improvement, question relevance and absence of
invented content still require evaluation against actual model outputs in separately
authorized work. No such calls or charges are needed by these tests.

M5B-3 extends the same fixture file with 36 human-reviewable reference cases. The
original nine authored outputs are explicitly marked `contract_example`; they
remain software fixtures, not unique quality reference answers. See the
[offline benchmark, rubric and future evaluation strategy](../../benchmarks/README.md).

M5B-4 adds the opt-in `create_architect_handoff()` function for existing request DTOs
and caller-owned run references, plus a blind Critic-input data projection. It
validates candidate/clarification semantics without changing this agent contract or
performing persistence. See the [integration and future transaction mapping](../../../docs/architect-service-integration.md).

## Independent Critic (M5C-2)

`PromptCriticAgent(gateway, model_settings).evaluate(critic_input, request_id=...)`
consumes the unchanged blind projection from `make_critic_input()`. It validates
exact evidence attribution and returns separate content/execution metadata through
one gateway operation. `ScoringEngine.score(result)` applies rubric 2.0.0 weights
and gates in Python using Decimal. Agent/instruction/scoring versions are 1.0.0.
Neither component performs persistence or activates an API/workflow. Fake tests
establish contracts and arithmetic, not real-model judgment quality. See
[contracts, uncertainty, evidence and benchmark mapping](../../../docs/prompt-critic.md).

M5C-4 adds the optional [single-pass coordinator](../../../docs/architect-critic-pass.md),
which reuses these agents and the existing handoff. It binds exact candidate/input
identity before deterministic scoring; clarification never reaches the Critic.
Agent contracts, independent instructions and borrowed-resource ownership are unchanged.
