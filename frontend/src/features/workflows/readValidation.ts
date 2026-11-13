import type { EvaluationSummary, WorkflowContentHistory, WorkflowMetadataHistory, WorkflowProgress, WorkflowResult,
  WorkflowVersionContent, WorkflowVersionSummary } from './contracts'
import { isTerminal } from './contracts'
import { WorkflowClientError } from './errors'
import { trustworthyProgress } from './progress'

// Relationship checks, not another scoring/selection policy. A valid JSON shape
// alone does not bind private text to the selected workflow or its evaluation.
export const sameId = (left: string | null, right: string | null) => left?.toLowerCase() === right?.toLowerCase()
function requireBinding(valid: boolean) {
  if (!valid) throw new Error('Invalid workflow relationship.')
}
export function assertWorkflow(row: WorkflowProgress, workflowId: string, parentId?: string) {
  requireBinding(sameId(row.id, workflowId) && (!parentId || sameId(row.prompt_request_id, parentId)))
}
function assertVersion(value: WorkflowVersionContent, workflow: WorkflowProgress) {
  requireBinding(sameId(value.version.workflow_run_id, workflow.id) &&
    sameId(value.version.prompt_request_id, workflow.prompt_request_id))
  requireEvaluationBinding(hasRendererIdentity(value.version))
}
function hasRendererIdentity(version: WorkflowVersionSummary): boolean {
  return Boolean(version.target_mode.trim() && version.renderer_name.trim() && version.renderer_version.trim())
}
function requireEvaluationBinding(valid: boolean) {
  if (!valid) throw new WorkflowClientError('malformed_response', 200, 'workflow_integrity_inconsistent')
}
function sameVersion(left: WorkflowVersionSummary, right: WorkflowVersionSummary): boolean {
  return sameId(left.id, right.id) && sameId(left.workflow_run_id, right.workflow_run_id) &&
    sameId(left.prompt_request_id, right.prompt_request_id) && sameId(left.agent_run_id, right.agent_run_id) &&
    sameId(left.parent_version_id, right.parent_version_id) && left.version_number === right.version_number &&
    left.evaluation_round === right.evaluation_round && left.target_mode === right.target_mode &&
    left.renderer_name === right.renderer_name && left.renderer_version === right.renderer_version
}
function sameEvaluation(left: EvaluationSummary, right: EvaluationSummary): boolean {
  return sameId(left.id, right.id) && sameId(left.prompt_version_id, right.prompt_version_id) &&
    sameId(left.critic_agent_run_id, right.critic_agent_run_id) && left.evaluation_round === right.evaluation_round
}
function unique<T>(rows: readonly T[], matches: (row: T) => boolean): T | undefined {
  const found = rows.filter(matches)
  return found.length === 1 ? found[0] : undefined
}
const sameVersionId = (a: WorkflowVersionSummary, b: WorkflowVersionSummary) => sameId(a.id, b.id)
const relatedEvaluation = (a: EvaluationSummary, b: EvaluationSummary) => sameId(a.id, b.id) ||
  sameId(a.prompt_version_id, b.prompt_version_id) || a.evaluation_round === b.evaluation_round ||
  sameId(a.critic_agent_run_id, b.critic_agent_run_id)

export function versionForEvaluation(value: WorkflowMetadataHistory, evaluation: EvaluationSummary) {
  const version = unique(value.versions, row => sameId(row.id, evaluation.prompt_version_id))
  const critic = unique(value.operations, row => sameId(row.id, evaluation.critic_agent_run_id))
  const stored = unique(value.evaluations, row => relatedEvaluation(row, evaluation))
  return stored && sameEvaluation(stored, evaluation) && version &&
    sameId(version.workflow_run_id, value.workflow.id) && sameId(version.prompt_request_id, value.workflow.prompt_request_id) &&
    version.evaluation_round === evaluation.evaluation_round && hasRendererIdentity(version) &&
    critic && sameId(critic.workflow_run_id, value.workflow.id) && critic.operation === 'evaluate' &&
    critic.evaluation_round === evaluation.evaluation_round && critic.status === 'COMPLETED' &&
    critic.result_kind === 'evaluation' && critic.finished_at !== null &&
    !critic.incomplete && !critic.unresolved ? version : undefined
}
function hasSharedConflict<T>(left: readonly T[], right: readonly T[], related: (a: T, b: T) => boolean,
  equal: (a: T, b: T) => boolean): boolean {
  return left.some(row => {
    const matches = right.filter(other => related(row, other))
    return matches.length > 1 || (matches.length === 1 && !equal(row, matches[0]!))
  })
}
function sameSelection(left: WorkflowProgress, right: WorkflowProgress): boolean {
  const a = left.selected, b = right.selected
  return left.status === right.status && sameId(left.stored_best_version_id, right.stored_best_version_id) &&
    sameId(a?.evaluation_id ?? null, b?.evaluation_id ?? null) &&
    sameId(a?.prompt_version_id ?? null, b?.prompt_version_id ?? null) &&
    a?.evaluation_round === b?.evaluation_round
}

export function reconcileResult(result: WorkflowResult, history: WorkflowMetadataHistory) {
  const { workflow, candidate, evaluation } = result
  if (!sameId(workflow.id, history.workflow.id) || !sameId(workflow.prompt_request_id, history.workflow.prompt_request_id) ||
    (candidate && hasSharedConflict([candidate.version], history.versions, sameVersionId, sameVersion)) ||
    (evaluation && hasSharedConflict([evaluation], history.evaluations, relatedEvaluation, sameEvaluation))) return 'conflict'
  if (history.workflow.state_revision < workflow.state_revision) return 'older'
  if (history.workflow.state_revision > workflow.state_revision) return 'newer'
  if (!sameSelection(workflow, history.workflow)) return 'conflict'
  if (candidate && evaluation) {
    const linked = versionForEvaluation(history, evaluation)
    if (!linked || !sameVersion(candidate.version, linked)) return 'conflict'
  }
  return 'current'
}

export function historiesConflict(left: WorkflowMetadataHistory, right: WorkflowMetadataHistory): boolean {
  if (!sameId(left.workflow.id, right.workflow.id) || !sameId(left.workflow.prompt_request_id, right.workflow.prompt_request_id) ||
    hasSharedConflict(left.versions, right.versions, sameVersionId, sameVersion) ||
    hasSharedConflict(left.evaluations, right.evaluations, relatedEvaluation, sameEvaluation)) return true
  if (left.workflow.state_revision !== right.workflow.state_revision) return false
  return !sameSelection(left.workflow, right.workflow) ||
    left.evaluations.length !== right.evaluations.length || left.versions.length !== right.versions.length ||
    left.evaluations.some(row => !unique(right.evaluations, other => sameEvaluation(row, other))) ||
    left.versions.some(row => !unique(right.versions, other => sameVersion(row, other)))
}
export function contentConflicts(content: WorkflowContentHistory, summary: WorkflowMetadataHistory | undefined,
  result: WorkflowResult | undefined): boolean {
  if (summary && trustworthyMetadata(summary) && historiesConflict(content.metadata, summary)) return true
  if (!result) return false
  const candidate = result.candidate
  return reconcileResult(result, content.metadata) === 'conflict' || Boolean(candidate &&
    content.versions.some(row => sameId(row.version.id, candidate.version.id) && row.rendered_prompt !== candidate.rendered_prompt))
}
export function assertContent(value: WorkflowContentHistory, workflowId: string, parentId?: string) {
  const metadata = value.metadata
  assertWorkflow(metadata.workflow, workflowId, parentId)
  requireBinding(!value.source || sameId(value.source.id, metadata.workflow.prompt_request_id))
  requireEvaluationBinding(new Set(value.versions.map(v => v.version.id.toLowerCase())).size === value.versions.length &&
    new Set(value.evaluations.map(v => v.evaluation_id.toLowerCase())).size === value.evaluations.length &&
    value.versions.length === value.evaluations.length)
  for (const candidate of value.versions) {
    assertVersion(candidate, metadata.workflow)
    const evaluation = unique(metadata.evaluations, row => sameId(row.prompt_version_id, candidate.version.id))
    const stored = evaluation && versionForEvaluation(metadata, evaluation)
    requireEvaluationBinding(Boolean(evaluation && stored && sameVersion(stored, candidate.version) &&
      (!value.source || value.source.target_mode === candidate.version.target_mode) &&
      unique(value.evaluations, assessment => sameId(assessment.evaluation_id, evaluation.id))))
  }
}
export function assertResult(value: WorkflowResult, workflowId: string, parentId?: string) {
  const row = value.workflow
  assertWorkflow(row, workflowId, parentId)
  requireBinding(isTerminal(row.status))
  requireEvaluationBinding(trustworthyProgress(row))
  const selected = row.selected
  if (!selected) {
    requireEvaluationBinding(value.candidate === null && value.evaluation === null && value.assessment === null)
    return
  }
  const { candidate, evaluation, assessment } = value
  requireEvaluationBinding(Boolean(candidate && evaluation && assessment &&
    sameId(candidate.version.id, selected.prompt_version_id) && sameId(evaluation.id, selected.evaluation_id) &&
    sameId(evaluation.prompt_version_id, selected.prompt_version_id) &&
    evaluation.evaluation_round === selected.evaluation_round &&
    candidate.version.evaluation_round === evaluation.evaluation_round &&
    evaluation.quality_status === selected.quality_status &&
    sameId(row.stored_best_version_id, selected.prompt_version_id)))
  if (candidate) assertVersion(candidate, row)
}
export function trustworthyMetadata(value: WorkflowMetadataHistory): boolean {
  return trustworthyProgress(value.workflow) && (!isTerminal(value.workflow.status) ||
    !value.operations.some(v => v.incomplete || v.unresolved))
}
