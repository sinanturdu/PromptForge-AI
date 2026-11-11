# LLM foundation (M5A-2 through M5A-4)

`LLMProvider` is an async, single-attempt Protocol with `generate()` and `aclose()`.
It has no HTTP, database, SDK or agent dependencies. `LLMGateway` borrows an injected
provider; its creator closes it. FastAPI lifespan composes one application-owned
provider and gateway, available as `app.state.llm_gateway`, and closes the provider
once on shutdown. `AsyncExitStack` also closes already-created resources if later
startup steps fail; database cleanup still runs if provider cleanup raises. An
injected SDK client retains its adapter-level ownership rules. Construction and
health checks never generate content. There is no standalone generation endpoint;
M5F's separately gated explicit workflow start uses this existing gateway through the
supervised optimizer. See [local operation and cost safety](../../../docs/local-mvp-operations.md).

`create_provider(settings, script=...)` supports `fake` (the default) and explicit
`openai`. Unsupported selections fail validation and never fall back silently.
Fake mode requires no credentials. OpenAI mode requires backend-only `OPENAI_API_KEY`,
held as `SecretStr`; missing/blank keys fail when composing the adapter. There is
no default model: callers specify `LLMRequest.settings.model` after checking account
access and capabilities. Never use a `VITE_` variable for a credential. No real
`.env` file is needed by tests. Provider choice is independent of stored
`target_mode` and `reasoning_preference`.

The fake consumes an instance-local copy of explicit `LLMResponse(provider="fake",
...)` or `LLMErrorCode` script entries in order. Empty scripts fail explicitly.
Close is idempotent, clears pending responses and prevents further generation.
It never invents output from a schema and its results are not genuine AI output.

Requests contain immutable message tuples and validated model settings. Temperature
is optional; 0–2 and 1–8192 output tokens are application bounds, not guarantees of
future model support. Unknown usage counters remain `None`; cached/reasoning counts
can be subsets and must not be blindly added. Frozen models are not deeply immutable
JSON containers; the gateway copies a request before assigning its output schema.

`generate()` and `generate_json()` share the gateway's bounded reliability policy.
They reject refusal, truncation, unknown finish states and missing text.
`generate_json(request, OutputModel)` derives the schema
from the supplied Pydantic model, validates locally with strict inputs and returns
`StructuredResult[OutputModel]` with value and content-free metadata. Output models
own semantic validation and their extra-field policy. Malformed JSON and schema
mismatch produce the same safe structured-output error after the repair budget is
exhausted. No billing estimates or persistence are introduced.

## Shared retry, repair and deadline limits

Configure the backend environment (see `.env.example`):

| Setting | Default | Accepted range |
| --- | --- | --- |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 20 | finite, greater than 0, up to 300 |
| `LLM_CALL_DEADLINE_SECONDS` | 90 | finite, greater than 0, up to 900 |
| `LLM_MAX_RETRIES` | 2 | integer 0–2 |
| `LLM_JSON_REPAIR_ATTEMPTS` | 1 | integer 0–1 |

The gateway is the only retry owner. It retries temporary rate limits, timeouts,
connection failures and HTTP 500/502/503/504. Authentication, permission, billing
quota, invalid configuration/schema/request, malformed envelopes, refusal and
truncation fail without retry or repair. Unexpected provider exceptions are
normalized safely and never retried. There are at most two transport retries across
the entire operation, including repair: three calls for plain text and **four calls
maximum** for structured output (one initial call + one repair + two retries).
Each concurrent operation has separate counters and deadlines.

Backoff uses equal jitter: 0.25–0.5 seconds, then 0.5–1.0 seconds. A valid provider
Retry-After can increase that wait; invalid/non-finite values are ignored. If the
wait cannot fit into the remaining deadline, the current provider failure is
returned immediately, preserving its delay metadata. The gateway never shortens
Retry-After to make another call. There are no autonomous/background retries.

Only otherwise complete, nonempty text with malformed JSON or schema mismatch can
trigger one repair. Repair preserves the original messages, settings and exact
schema, appends the invalid response plus a fixed correction instruction and a
small allowlist of validation categories, then validates again locally. Validation
values, custom error messages and paths are excluded from feedback. Repair corrects
JSON/schema defects; it does not advance any agent quality/evaluation round.

One monotonic deadline covers schema generation, calls, backoff, validation and
repair. Python 3.12 `asyncio.timeout()` bounds each call by the smaller of the
per-attempt timeout and remaining deadline, and bounds backoff by the remaining
deadline. External task cancellation propagates; calls run in the calling task,
so timeout does not leave a detached generation task. CPU-bound synchronous
validation and cancellation-suppressing custom providers cannot be forcibly
preempted; elapsed-time checks reject late results before another call or success.

`StructuredResult.metadata`, `LLMResponse.call_metadata` and
`LLMError.metadata.call` report attempts, retries, repair usage, elapsed milliseconds
and aggregate **observed** usage. `LLMResponse.usage` remains the final response's
usage. Each aggregate counter sums only known values and stays `None` if entirely
unknown. `usage_complete` is true only when all five counters were available for
every call. Failed/time-out calls may have incurred unreported usage; observed
totals with `usage_complete=false` are not a billing ledger. Cached/reasoning
counts are subsets, never added again to total tokens.

Terminal diagnostics use the existing safe logger with an explicit allowlist of
provider/model identifiers, counts, duration, error category and numeric usage.
No prompts, results, repair payloads, keys, raw exceptions or complete objects are
logged. Clock, sleep and jitter functions can be injected for deterministic tests.

## OpenAI adapter

The lockfile pins the official OpenAI Python SDK to **2.54.0**. The adapter uses
`AsyncOpenAI.responses.create()` once per generation, with `stream=False`,
`store=False`, `max_retries=0`, and the configured per-attempt SDK transport timeout
(20 seconds by default). The gateway also enforces the operation deadline. Locally
created HTTP clients disable redirects to prevent extra unbudgeted requests.
The official endpoint is explicit, so an
inherited `OPENAI_BASE_URL` cannot redirect a locally created client. No model or
account access is inferred. Construction and application startup make no API call.

System/user/assistant messages preserve their original order and exact content.
Temperature is omitted by default. To send it, a caller must explicitly declare
the exact verified model ID in the adapter's `temperature_models` set. The standard
composition uses the empty set and rejects explicit temperature. No model-name
heuristic silently drops or rewrites a requested option.

Structured output uses `text.format` with `type=json_schema`, the stable name
`promptforge_output`, and `strict=True`. The preflight deliberately supports a
conservative subset: object roots, required properties, `additionalProperties=false`,
primitive/nullable types, arrays, enums/constants, nested `anyOf`, and local
`$defs`/`$ref`. It checks basic depth, property, enum and string-size limits.
Defaults, validation keywords such as `minLength`, external references and unknown
keywords fail before transmission. Nothing is silently stripped or converted to
plain JSON mode. Use output models with `extra="forbid"` and required nullable
fields rather than defaulted optional fields. The provider remains authoritative
for model-specific restrictions; local gateway validation is always retained.
See [official Structured Outputs guidance](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

Response envelopes are locally revalidated with serialization warnings disabled
to avoid warning messages containing private outputs. Completion status is checked
before trusting text. Refusal content and content-filter interruption map to refusal;
output-token interruption maps to truncation; malformed/unknown envelopes fail safely.
Reasoning items are permitted but never exposed as text. Missing usage stays `None`;
reported totals are copied, never recomputed by adding cached/reasoning subsets.

SDK failures map to fixed normalized categories, with only validated request IDs,
HTTP status and a parsed Retry-After delay retained. Request IDs outside the bounded
`req_` identifier shape are omitted. Numeric seconds, milliseconds and HTTP dates
are supported; the adapter only reports delays and the gateway schedules retries. Raw exceptions,
bodies and exception chains are discarded. Adapter programming errors and task
cancellation propagate. The existing gateway separately retains its general safety
boundary. SDK DEBUG logging is disabled when composing an OpenAI adapter because
it can include request content; do not re-enable it or attach unrestricted local
variable capture to error monitoring.

The adapter owns locally created clients. An injected SDK client is borrowed unless
`owns_client=True` explicitly transfers ownership; its creator must configure the
transport for single requests (no hidden retries/redirects) and set `max_retries=0`.
Retrying clients are rejected. Close is idempotent;
closing a borrowed adapter does not close its client. See the
[official SDK documentation](https://developers.openai.com/api/reference/python).

`store=False` disables response storage for this operation; it is not a promise of
zero retention under every account policy. See
[OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

Run `uv run pytest tests/test_openai_provider.py` for offline adapter verification.
Tests use the actual SDK with `httpx.MockTransport`, synthetic credentials and no
real API calls. Backend tests force fake mode before collection and block external
socket connections. E2E startup forces fake mode before application imports; each
browser scenario keeps its existing separate disposable SQLite database.

Run `uv run pytest tests/test_llm_reliability.py` for deterministic retry, repair,
deadline, cancellation, usage, privacy and lifespan tests. Existing single-attempt
contract/adapter tests explicitly disable gateway retries and repair to retain
their original scope. No agents, public AI endpoints, workflows or database
migrations are introduced here.

Errors expose a category, fixed message and content-free metadata. Cancellation propagates. The
gateway logs only allowlisted diagnostics and retains no prompt history. Content fields are excluded
from ordinary representations; ordinary Pydantic errors hide input values.
**Never log model dumps, `ValidationError.errors()`/`.json()`, generated values or
raw provider exceptions.** Those can contain private content despite safe reprs.
Metadata IDs are untrusted strings, not a blanket logging allowlist. Exception
tracebacks that capture locals and error-monitoring integrations need separate
redaction. No real `.env` file or credential is needed for the offline tests.
