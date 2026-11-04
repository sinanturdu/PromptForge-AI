# LLM foundation (M5A-2)

`LLMProvider` is an async, single-attempt Protocol with `generate()` and `aclose()`.
It has no HTTP, database, SDK or agent dependencies. `LLMGateway` borrows an injected
provider; the creator must close that provider in a `finally` block. No provider is
created by application startup and no AI endpoint is exposed in this milestone.

`create_provider(settings, script=...)` selects only `fake`. `LLM_PROVIDER` defaults
to `fake`; other selections fail configuration validation, never fall back silently.
No credentials or model environment settings are needed. This internal choice is
independent of stored `target_mode` and `reasoning_preference`.

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
retry/repair scheduling is deferred to M5A-4. Real adapters belong to M5A-3.

Errors expose only a category and fixed message. Cancellation propagates. The
package emits no logs and retains no prompt history. Content fields are excluded
from ordinary representations; ordinary Pydantic errors hide input values.
**Never log model dumps, `ValidationError.errors()`/`.json()`, generated values or
raw provider exceptions.** Those can contain private content despite safe reprs.
Metadata IDs are untrusted strings, not a blanket logging allowlist. Exception
tracebacks that capture locals and error-monitoring integrations need separate
redaction. No real `.env` file or credential is needed for the offline tests.
