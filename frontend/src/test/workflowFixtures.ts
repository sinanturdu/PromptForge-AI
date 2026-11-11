import type { WorkflowProgress, WorkflowMetadataHistory, WorkflowContentHistory, WorkflowResult } from '../features/workflows/contracts'

export const PARENT = '10000000-0000-0000-0000-000000000001'
export const RUN = '20000000-0000-0000-0000-000000000002'
export const VERSION = '30000000-0000-0000-0000-000000000003'
export const EVALUATION = '40000000-0000-0000-0000-000000000004'
export const DATE = '2026-11-06T12:00:00Z'
export const PRIVATE = 'PRIVATE_WORKFLOW_PROMPT_SENTINEL'
export function progress(update: Partial<WorkflowProgress> = {}): WorkflowProgress {
  return { id: RUN, prompt_request_id: PARENT, status: 'PENDING', state_revision: 0,
    current_round: 0, completed_rounds: 0, max_evaluation_rounds: 3, created_at: DATE,
    started_at: null, finished_at: null, stored_best_version_id: null, stored_final_score: null,
    stored_qualified: null, termination_reason: null, error_code: null, clarification_question_count: 0,
    selected: null, history_valid: true, records_truncated: false, findings: [], ...update }
}
export function metadata(): WorkflowMetadataHistory {
  return { workflow: progress(), operations: [{ id: VERSION, workflow_run_id: RUN, operation: 'create',
    evaluation_round: 1, status: 'RUNNING', started_at: DATE, finished_at: null, result_kind: null,
    telemetry: null, error_code: null, incomplete: true, unresolved: true, staged_unevaluated_candidate: false }],
    versions: [], evaluations: [] }
}
export function content(): WorkflowContentHistory {
  return { metadata: metadata(), source: { id: PARENT, raw_prompt: PRIVATE, user_context: null,
    output_language: null, target_mode: 'generic', task_profile: null, prompt_detail_level: 'medium',
    response_detail_preference: 'balanced', reasoning_preference: 'auto', created_at: DATE },
    versions: [], evaluations: [], clarification_questions: [] }
}
export function result(): WorkflowResult {
  return { workflow: progress({ status: 'CLARIFICATION_REQUIRED', termination_reason: 'clarification_required',
    clarification_question_count: 1 }), candidate: null, evaluation: null, assessment: null,
    clarification_questions: ['Which output is required?'] }
}
