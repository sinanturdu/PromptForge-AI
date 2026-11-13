import type { PromptFormValues, PromptRequestRecord } from '../prompt-requests/types'
import { characterCount, isBlank } from '../prompt-requests/validation'
import { sourceRequest, type WorkflowContentHistory, type WorkflowMetadataHistory, type WorkflowProgress, type WorkflowResult } from './contracts'
import { trustworthyProgress } from './progress'
import { contentConflicts, reconcileResult, sameId, trustworthyMetadata } from './readValidation'

export interface FollowupSource {
  readonly record: PromptRequestRecord
  readonly queryId: string
  readonly checking?: boolean
}

export function checkedSource(source: FollowupSource | undefined, parentId: string) {
  if (!source || !sameId(source.queryId, parentId)) return undefined
  try {
    const record = sourceRequest(source.record)
    return sameId(record.id, parentId) && !isBlank(record.raw_prompt) ? record : undefined
  } catch {
    return undefined
  }
}

export function sameSource(a: PromptRequestRecord, b: PromptRequestRecord): boolean {
  return sameId(a.id, b.id) && a.raw_prompt === b.raw_prompt && a.user_context === b.user_context &&
    a.output_language === b.output_language && a.task_profile === b.task_profile && a.target_mode === b.target_mode &&
    a.prompt_detail_level === b.prompt_detail_level && a.response_detail_preference === b.response_detail_preference &&
    a.reasoning_preference === b.reasoning_preference && a.created_at === b.created_at
}

export function sameQuestions(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((question, index) => question === b[index])
}

export function validClarificationQuestions(workflow: WorkflowProgress, questions: readonly string[]): boolean {
  return workflow.status === 'CLARIFICATION_REQUIRED' && validClarificationCount(workflow) &&
    workflow.clarification_question_count === questions.length &&
    questions.every(question => !isBlank(question) && characterCount(question) <= 1000)
}

export function validClarificationCount(workflow: WorkflowProgress): boolean {
  const count = workflow.clarification_question_count
  return workflow.status === 'CLARIFICATION_REQUIRED' ? count >= 1 && count <= 3 : count === 0
}

export function clarificationContentConflicts(source: PromptRequestRecord, result: WorkflowResult | undefined, content: WorkflowContentHistory): boolean {
  return Boolean((content.source ? !sameSource(source, content.source) : content.metadata.workflow.status === 'CLARIFICATION_REQUIRED') ||
    (result && content.metadata.workflow.state_revision === result.workflow.state_revision &&
      (content.metadata.workflow.clarification_question_count !== result.clarification_questions.length ||
        !sameQuestions(result.clarification_questions, content.clarification_questions))))
}

export function boundQuestions(workflowId: string, parentId: string, source: PromptRequestRecord | undefined,
  progress: WorkflowProgress | undefined, result: WorkflowResult | undefined, summary: WorkflowMetadataHistory | undefined,
  content: WorkflowContentHistory | undefined) {
  if (!source || !progress || !result || !summary || !sameId(source.id, parentId)) return undefined
  const { workflow, clarification_questions: questions } = result
  if (workflow.status !== 'CLARIFICATION_REQUIRED' || progress.status !== workflow.status ||
    !sameId(workflow.id, workflowId) || !sameId(progress.id, workflowId) ||
    !sameId(workflow.prompt_request_id, parentId) || !sameId(progress.prompt_request_id, parentId) ||
    !trustworthyProgress(workflow) || !trustworthyProgress(progress) || !trustworthyMetadata(summary) ||
    progress.state_revision > workflow.state_revision || reconcileResult(result, summary) !== 'current' ||
    reconcileResult(result, { ...summary, workflow: progress }) === 'conflict' ||
    !validClarificationQuestions(workflow, questions) ||
    [workflow, progress, summary.workflow].some(row => row.clarification_question_count !== questions.length) ||
    (result.candidate && result.candidate.version.target_mode !== source.target_mode) ||
    summary.versions.some(version => version.target_mode !== source.target_mode)) return undefined

  if (content && (!trustworthyMetadata(content.metadata) || contentConflicts(content, summary, result) ||
    content.metadata.workflow.state_revision > workflow.state_revision ||
    clarificationContentConflicts(source, result, content))) return undefined
  return questions
}

export function buildFollowupDraft(source: PromptRequestRecord, questions: readonly string[], answers: readonly string[]): PromptFormValues {
  const pairs = questions.map((question, index) => `Soru ${index + 1}: ${question}\nYanıt ${index + 1}: ${answers[index]}`)
  return {
    rawPrompt: source.raw_prompt,
    userContext: [source.user_context, 'Ek Açıklama Yanıtları', ...pairs].filter(Boolean).join('\n\n'),
    outputLanguage: source.output_language ?? '',
    taskProfile: source.task_profile ?? '',
    targetMode: source.target_mode,
    promptDetailLevel: source.prompt_detail_level,
    responseDetailPreference: source.response_detail_preference,
    reasoningPreference: source.reasoning_preference,
  }
}
