import type { WorkflowContentHistory, WorkflowMetadataHistory, WorkflowProgress, WorkflowResult,
  WorkflowVersionContent } from './contracts'
import { isTerminal } from './contracts'
import { trustworthyProgress } from './progress'

// Relationship checks, not another scoring/selection policy. A valid JSON shape
// alone does not bind private text to the selected workflow or its evaluation.
const same = (left: string | null, right: string | null) => left?.toLowerCase() === right?.toLowerCase()
function requireBinding(valid: boolean) {
  if (!valid) throw new Error('Invalid workflow relationship.')
}
export function assertWorkflow(row: WorkflowProgress, workflowId: string, parentId?: string) {
  requireBinding(same(row.id, workflowId) && (!parentId || same(row.prompt_request_id, parentId)))
}
function assertVersion(value: WorkflowVersionContent, workflow: WorkflowProgress) {
  requireBinding(same(value.version.workflow_run_id, workflow.id) &&
    same(value.version.prompt_request_id, workflow.prompt_request_id))
}
export function assertContent(value: WorkflowContentHistory, workflowId: string, parentId?: string) {
  const metadata = value.metadata
  assertWorkflow(metadata.workflow, workflowId, parentId)
  requireBinding(!value.source || same(value.source.id, metadata.workflow.prompt_request_id))
  requireBinding(new Set(value.versions.map(v => v.version.id)).size === value.versions.length &&
    new Set(value.evaluations.map(v => v.evaluation_id)).size === value.evaluations.length)
  for (const candidate of value.versions) {
    assertVersion(candidate, metadata.workflow)
    const stored = metadata.versions.find(v => same(v.id, candidate.version.id))
    requireBinding(Boolean(stored && same(stored.workflow_run_id, metadata.workflow.id) &&
      same(stored.prompt_request_id, metadata.workflow.prompt_request_id) &&
      same(stored.agent_run_id, candidate.version.agent_run_id) &&
      stored.version_number === candidate.version.version_number &&
      stored.evaluation_round === candidate.version.evaluation_round &&
      metadata.evaluations.some(v => same(v.prompt_version_id, stored.id) &&
        v.evaluation_round === stored.evaluation_round)))
  }
  for (const assessment of value.evaluations) {
    const stored = metadata.evaluations.find(v => same(v.id, assessment.evaluation_id))
    requireBinding(Boolean(stored && value.versions.some(v => same(v.version.id, stored.prompt_version_id)) &&
      metadata.operations.some(v => same(v.id, stored.critic_agent_run_id) && same(v.workflow_run_id, workflowId) &&
        v.operation === 'evaluate' && v.evaluation_round === stored.evaluation_round &&
        v.status === 'COMPLETED' && !v.incomplete && !v.unresolved)))
  }
}
export function assertResult(value: WorkflowResult, workflowId: string, parentId?: string) {
  const row = value.workflow
  assertWorkflow(row, workflowId, parentId)
  requireBinding(isTerminal(row.status) && trustworthyProgress(row))
  const selected = row.selected
  if (!selected) {
    requireBinding(value.candidate === null && value.evaluation === null && value.assessment === null)
    return
  }
  const { candidate, evaluation, assessment } = value
  requireBinding(Boolean(candidate && evaluation && assessment &&
    same(candidate.version.id, selected.prompt_version_id) && same(evaluation.id, selected.evaluation_id) &&
    same(evaluation.prompt_version_id, selected.prompt_version_id) &&
    evaluation.evaluation_round === selected.evaluation_round &&
    candidate.version.evaluation_round === evaluation.evaluation_round &&
    evaluation.quality_status === selected.quality_status &&
    same(row.stored_best_version_id, selected.prompt_version_id)))
  if (candidate) assertVersion(candidate, row)
}
export function trustworthyMetadata(value: WorkflowMetadataHistory): boolean {
  return trustworthyProgress(value.workflow) && (!isTerminal(value.workflow.status) ||
    !value.operations.some(v => v.incomplete || v.unresolved))
}
