# LLM foundation (M5A-2 / M5A-3)

`LLMProvider` is an async, single-attempt Protocol with `generate()` and `aclose()`.
It has no HTTP, database, SDK or agent dependencies. `LLMGateway` borrows an injected
provider; the creator must close that provider in a `finally` block. No provider is
created by application startup and no AI endpoint is exposed in this milestone.

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

`generate()` makes at most one call and rejects refusal, truncation, unknown finish
states and missing text. `generate_json(request, OutputModel)` derives the schema
from the supplied Pydantic model, validates locally with strict inputs and returns
`StructuredResult[OutputModel]` with value and content-free metadata. Output models
own semantic validation and their extra-field policy. Malformed JSON and schema
mismatch produce the same safe structured-output error. There are no retries,
repair calls, sleeps, deadlines, billing estimates or persistence in M5A-2; bounded
retry/repair scheduling is deferred to M5A-4.

## OpenAI adapter

The lockfile pins the official OpenAI Python SDK to **2.54.0**. The adapter uses
`AsyncOpenAI.responses.create()` once per generation, with `stream=False`,
`store=False`, `max_retries=0`, and a 20-second SDK transport timeout. This timeout
is not an overall operation deadline. The official endpoint is explicit, so an
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
are supported; delays are metadata only, with no sleeping/retry. Raw exceptions,
bodies and exception chains are discarded. Adapter programming errors and task
cancellation propagate. The existing gateway separately retains its general safety
boundary. SDK DEBUG logging is disabled when composing an OpenAI adapter because
it can include request content; do not re-enable it or attach unrestricted local
variable capture to error monitoring.

The adapter owns locally created clients. An injected SDK client is borrowed unless
`owns_client=True` explicitly transfers ownership; its creator must configure the
transport and set `max_retries=0`. Retrying clients are rejected. Close is idempotent;
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

M5A-4 still owns bounded transport retries, JSON repair, backoff, overall deadlines
and production FastAPI lifespan wiring. No agents, public AI endpoints, workflows
or database migrations are introduced here.

Errors expose a category, fixed message and content-free metadata. Cancellation propagates. The
package emits no logs and retains no prompt history. Content fields are excluded
from ordinary representations; ordinary Pydantic errors hide input values.
**Never log model dumps, `ValidationError.errors()`/`.json()`, generated values or
raw provider exceptions.** Those can contain private content despite safe reprs.
Metadata IDs are untrusted strings, not a blanket logging allowlist. Exception
tracebacks that capture locals and error-monitoring integrations need separate
redaction. No real `.env` file or credential is needed for the offline tests.
