// Wire projections of workflow_api_schemas.py (serialization mode). Required keys
// and nulls are retained. Decimal scores are JSON strings, never coerced to numbers.
// Types derive from these checked parsers so validation and TS cannot drift apart.
import type { PromptRequestRecord } from '../prompt-requests/types'
type Parser<T> = (value: unknown) => T
export type Parsed<P> = P extends Parser<infer T> ? T : never

function invalid(): never { throw new Error('Invalid workflow contract.') }
const text: Parser<string> = v => typeof v === 'string' ? v : invalid()
const bool: Parser<boolean> = v => typeof v === 'boolean' ? v : invalid()
const number: Parser<number> = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : invalid()
const integer = (min = 0, max = Number.MAX_SAFE_INTEGER): Parser<number> => v =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max ? v : invalid()
export const uuid: Parser<string> = v => typeof v === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v) ? v : invalid()
const timestamp: Parser<string> = v => typeof v === 'string' && Number.isFinite(Date.parse(v)) ? v : invalid()
const decimal: Parser<string> = v => typeof v === 'string' &&
  /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(v) ? v : invalid()
const nullable = <T>(parse: Parser<T>): Parser<T | null> => v => v === null ? null : parse(v)
const array = <T>(parse: Parser<T>): Parser<readonly T[]> => v => Array.isArray(v) ? v.map(parse) : invalid()
function choice<const T extends readonly (string | boolean)[]>(...values: T): Parser<T[number]> {
  return v => values.some(item => item === v) ? v as T[number] : invalid()
}
function object<const S extends Record<string, Parser<unknown>>>(shape: S):
  Parser<{ readonly [K in keyof S]: Parsed<S[K]> }> {
  return value => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return invalid()
    const row = value as Record<string, unknown>
    if (Object.keys(row).some(key => !Object.hasOwn(shape, key))) return invalid()
    return Object.fromEntries(Object.entries(shape).map(([key, parse]) => [key, parse(row[key])])) as
      { readonly [K in keyof S]: Parsed<S[K]> }
  }
}

const status = choice('PENDING', 'GENERATING', 'EVALUATING', 'REVISING', 'COMPLETED', 'FAILED', 'CLARIFICATION_REQUIRED')
const quality = choice('qualified', 'not_qualified', 'unassessable')
const round = integer(0, 3)
const errorCode = choice('invalid_configuration', 'missing_credentials', 'authentication', 'permission',
  'quota_exhausted', 'rate_limit', 'timeout', 'provider_unavailable', 'invalid_request',
  'invalid_structured_output', 'refusal', 'truncation', 'missing_text', 'script_exhausted',
  'provider_closed', 'invalid_response', 'deadline_exceeded', 'interrupted', 'persistence_failure', 'internal_error')
const termination = choice('quality_passed', 'evaluation_limit', 'no_actionable_feedback',
  'unchanged_baseline', 'already_evaluated', 'repeated_revision_attempt', 'clarification_required',
  'provider_failure', 'validation_failure', 'deadline_exceeded', 'interrupted', 'persistence_failure', 'internal_error')
const identityFields = { id: uuid, prompt_request_id: uuid, status }
const identity = object(identityFields)
export const createResponse = object({ workflow: identity, replayed: bool })
export const startResponse = object({ workflow: identity, accepted: choice(true) })
export const integrityFinding = object({
  code: choice('source_binding', 'history_size', 'operation_provenance', 'operation_state',
    'operation_telemetry', 'version_lineage', 'evaluation_provenance', 'history_validation',
    'progress_selection', 'workflow_state', 'terminal_state'),
  scope: choice('workflow', 'source', 'operation', 'version', 'evaluation', 'history', 'terminal'),
  record_id: nullable(uuid),
})
export const gates = object({ all_criteria_assessable: bool, total_at_least_85: nullable(bool),
  no_critical_issues: bool, intent_at_least_7: nullable(bool) })
export const selectedEvaluation = object({ evaluation_id: uuid, prompt_version_id: uuid,
  evaluation_round: integer(), total_score: nullable(decimal), qualified: bool, quality_status: quality,
  computed_selection_policy_version: text })
export const progressResponse = object({ ...identityFields, state_revision: integer(),
  current_round: round, completed_rounds: round, max_evaluation_rounds: integer(1, 3),
  created_at: timestamp, started_at: nullable(timestamp), finished_at: nullable(timestamp),
  stored_best_version_id: nullable(uuid), stored_final_score: nullable(decimal),
  stored_qualified: nullable(bool), termination_reason: nullable(termination), error_code: nullable(errorCode),
  clarification_question_count: integer(), selected: nullable(selectedEvaluation),
  history_valid: bool, records_truncated: bool, findings: array(integrityFinding) })
export const cursor = object({ created_at: timestamp, workflow_run_id: uuid })
export const listResponse = object({
  items: array(object({ ...identityFields, created_at: timestamp, finished_at: nullable(timestamp),
    current_round: round, completed_rounds: round, integrity_checked: choice(false) })),
  next_cursor: nullable(cursor),
})
export const telemetry = object({ usage: nullable(object({ input_tokens: nullable(integer()),
  output_tokens: nullable(integer()), total_tokens: nullable(integer()), cached_input_tokens: nullable(integer()),
  reasoning_tokens: nullable(integer()) })), attempts: integer(), retries: integer(),
  repair_attempted: bool, duration_ms: number, usage_complete: bool })
export const operation = object({ id: uuid, workflow_run_id: uuid,
  operation: choice('create', 'revise', 'evaluate'), evaluation_round: nullable(integer()),
  status: choice('PENDING', 'RUNNING', 'COMPLETED', 'FAILED'), started_at: timestamp,
  finished_at: nullable(timestamp), result_kind: nullable(choice('candidate', 'clarification', 'unchanged', 'evaluation', 'failed')),
  telemetry: nullable(telemetry), error_code: nullable(errorCode), incomplete: bool, unresolved: bool,
  staged_unevaluated_candidate: bool })
export const versionSummary = object({ id: uuid, prompt_request_id: uuid, workflow_run_id: nullable(uuid),
  agent_run_id: nullable(uuid), parent_version_id: nullable(uuid), version_number: integer(),
  evaluation_round: nullable(integer()), source: text, target_mode: text, renderer_name: text,
  renderer_version: text, created_at: timestamp })
export const evaluationSummary = object({ id: uuid, prompt_version_id: uuid, critic_agent_run_id: uuid,
  evaluation_round: integer(), completed_at: timestamp, rubric_version: text, scoring_version: text,
  stored_total_score: nullable(decimal), quality_status: nullable(quality), gates: nullable(gates) })
export const metadataResponse = object({ workflow: progressResponse, operations: array(operation),
  versions: array(versionSummary), evaluations: array(evaluationSummary) })
export const canonicalPrompt = object({ task: text, role: nullable(text), context: nullable(text),
  input_data: nullable(text), output_format: nullable(text), requirements: array(text), constraints: array(text),
  prohibitions: array(text), examples: array(text), variables: array(text), assumptions: array(text) })
const evidence = object({ source: choice('raw_prompt', 'user_context', 'output_language', 'task_profile',
  'prompt_detail_level', 'response_detail_preference', 'reasoning_preference', 'target_mode', 'rendered_prompt'), quote: text })
const criterion = object({ score: nullable(integer(0, 10)), rationale: text, evidence: array(evidence) })
const criterionKey = choice('intent_fidelity', 'clarity', 'completeness', 'structure', 'context_and_role',
  'constraints_and_scope', 'output_format', 'specificity', 'consistency')
export const criticOutput = object({
  criteria: object({ intent_fidelity: criterion, clarity: criterion, completeness: criterion, structure: criterion,
    context_and_role: criterion, constraints_and_scope: criterion, output_format: criterion, specificity: criterion,
    consistency: criterion }),
  issues: array(object({ criterion: criterionKey, code: choice('intent_shift', 'missing_requirement',
    'invented_requirement', 'contradiction', 'unsupported_context', 'unnecessary_complexity',
    'insufficient_specificity', 'task_mismatch', 'format_mismatch'), status: choice('supported', 'uncertain'),
    severity: nullable(choice('critical', 'major', 'minor')), description: text, recommendation: text,
    evidence: array(evidence) })), summary: text,
})
const sourceRequest: Parser<PromptRequestRecord> = object({ id: uuid, raw_prompt: text, user_context: nullable(text), output_language: nullable(text),
  target_mode: choice('generic', 'openai', 'anthropic', 'google'), task_profile: nullable(text),
  prompt_detail_level: choice('low', 'medium', 'high'), response_detail_preference: choice('minimal', 'balanced', 'comprehensive'),
  reasoning_preference: choice('auto', 'low', 'medium', 'high'), created_at: timestamp })
export const versionContent = object({ version: versionSummary, canonical_prompt: canonicalPrompt,
  rendered_prompt: text, change_summary: array(text) })
export const contentResponse = object({ metadata: metadataResponse, source: nullable(sourceRequest),
  versions: array(versionContent), evaluations: array(object({ evaluation_id: uuid, assessment: criticOutput })),
  clarification_questions: array(text) })
export const resultResponse = object({ workflow: progressResponse, candidate: nullable(versionContent),
  evaluation: nullable(evaluationSummary), assessment: nullable(criticOutput), clarification_questions: array(text) })

export type WorkflowStatus = Parsed<typeof status>
export type QualityStatus = Parsed<typeof quality>
export type IntegrityFinding = Parsed<typeof integrityFinding>
export type WorkflowIdentity = Parsed<typeof identity>
export type WorkflowCreateResponse = Parsed<typeof createResponse>
export type WorkflowStartResponse = Parsed<typeof startResponse>
export type WorkflowProgress = Parsed<typeof progressResponse>
export type WorkflowCursor = Parsed<typeof cursor>
export type WorkflowList = Parsed<typeof listResponse>
export type WorkflowTelemetry = Parsed<typeof telemetry>
export type WorkflowOperation = Parsed<typeof operation>
export type WorkflowVersionSummary = Parsed<typeof versionSummary>
export type CanonicalPrompt = Parsed<typeof canonicalPrompt>
export type CriticOutput = Parsed<typeof criticOutput>
export type GateResults = Parsed<typeof gates>
export type EvaluationSummary = Parsed<typeof evaluationSummary>
export type SelectedEvaluation = Parsed<typeof selectedEvaluation>
export type WorkflowMetadataHistory = Parsed<typeof metadataResponse>
export type WorkflowContentHistory = Parsed<typeof contentResponse>
export type WorkflowResult = Parsed<typeof resultResponse>
export type WorkflowVersionContent = Parsed<typeof versionContent>

export interface WorkflowCreateRequest {
  readonly client_idempotency_key: string
  readonly max_evaluation_rounds?: 1 | 2 | 3
}
export interface WorkflowStartRequest { readonly expected_revision: number }
export interface WorkflowListParams { readonly limit?: number; readonly before?: WorkflowCursor }

export function isTerminal(status: WorkflowStatus): boolean {
  return status === 'COMPLETED' || status === 'FAILED' || status === 'CLARIFICATION_REQUIRED'
}
export function trustedSelection(progress: WorkflowProgress): SelectedEvaluation | null {
  return progress.history_valid && !progress.records_truncated && progress.findings.length === 0
    ? progress.selected : null
}
