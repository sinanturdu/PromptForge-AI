import type { CriticOutput, QualityStatus, WorkflowContentHistory, WorkflowMetadataHistory, WorkflowResult } from '../features/workflows/contracts'
import type { TargetMode } from '../features/prompt-requests/types'
import { content, DATE, EVALUATION, PARENT, progress, RUN, VERSION } from './workflowFixtures'

export const ORIGINAL = '  Explain Python 3.12.\n```python\ndef f():\n    return 1\n```\n'
export const RENDERED = '  Explain Python 3.12. Keep Merhaba.\n```python\ndef f():\n    return 1\n```\n<img src=x onerror=alert(1)>\n'
export const CREATOR = '50000000-0000-0000-0000-000000000005'
export const CRITIC = '60000000-0000-0000-0000-000000000006'
export const OTHER_RUN = '70000000-0000-0000-0000-000000000007'

// Wire-contract fixtures, not a claim that fake scores establish model quality.
export function resultFixture(quality: QualityStatus = 'qualified', score: string | null = '100.0000',
  identity: { target_mode: TargetMode; renderer_name: string; renderer_version: string } = {
    target_mode: 'generic', renderer_name: 'GenericPromptRenderer', renderer_version: '1.0.0',
  }) {
  const scored: CriticOutput['criteria']['intent_fidelity'] = { score: 10,
    rationale: 'Kayıtlı gerekçe.', evidence: [{ source: 'raw_prompt', quote: 'Python 3.12' }] }
  const assessment: CriticOutput = { criteria: { intent_fidelity: scored,
    clarity: quality === 'unassessable' ? { ...scored, score: null } : scored,
    completeness: scored, structure: scored, context_and_role: scored, constraints_and_scope: scored,
    output_format: scored, specificity: scored, consistency: scored },
  summary: 'Kayıtlı bağımsız değerlendirme.', issues: [] }
  const candidate: NonNullable<WorkflowResult['candidate']> = { version: { id: VERSION, prompt_request_id: PARENT,
    workflow_run_id: RUN, agent_run_id: CREATOR, parent_version_id: null, version_number: 2,
    evaluation_round: 1, source: 'architect', ...identity, created_at: DATE },
  canonical_prompt: { task: RENDERED, role: null, context: null, input_data: null, output_format: null,
    requirements: [], constraints: [], prohibitions: [], examples: [], variables: [], assumptions: [] },
  rendered_prompt: RENDERED, change_summary: ['Kayıtlı Architect notu.'] }
  const evaluation: NonNullable<WorkflowResult['evaluation']> = { id: EVALUATION, prompt_version_id: VERSION,
    critic_agent_run_id: CRITIC, evaluation_round: 1, completed_at: DATE, rubric_version: '2.0.0', scoring_version: '1.0.0',
    stored_total_score: score, quality_status: quality, gates: { all_criteria_assessable: quality !== 'unassessable',
      total_at_least_85: quality === 'unassessable' ? null : true, no_critical_issues: quality !== 'not_qualified', intent_at_least_7: true } }
  const workflow = progress({ status: 'COMPLETED', state_revision: 8, current_round: 1, completed_rounds: 1,
    stored_best_version_id: VERSION, stored_final_score: score, stored_qualified: quality === 'qualified',
    finished_at: DATE, started_at: DATE, termination_reason: 'quality_passed', selected: { evaluation_id: EVALUATION,
      prompt_version_id: VERSION, evaluation_round: 1, total_score: score, qualified: quality === 'qualified',
      quality_status: quality, computed_selection_policy_version: '1.0.0' } })
  const result: WorkflowResult = { workflow, candidate, evaluation, assessment, clarification_questions: [] }
  const metadata: WorkflowMetadataHistory = { workflow, versions: [candidate.version], evaluations: [evaluation],
    operations: [CREATOR, CRITIC].map((id, index) => ({ id, workflow_run_id: RUN, operation: index === 0 ? 'create' : 'evaluate',
      evaluation_round: 1, status: 'COMPLETED', started_at: DATE, finished_at: DATE,
      result_kind: index === 0 ? 'candidate' : 'evaluation', telemetry: null, error_code: null,
      incomplete: false, unresolved: false, staged_unevaluated_candidate: false })) }
  const history: WorkflowContentHistory = { metadata, source: { ...content().source!, raw_prompt: ORIGINAL, target_mode: identity.target_mode },
    versions: [candidate], evaluations: [{ evaluation_id: EVALUATION, assessment }], clarification_questions: [] }
  return { result, metadata, history }
}
