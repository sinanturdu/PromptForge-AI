import { mutationOptions, queryOptions, type QueryClient } from '@tanstack/react-query'
import { promptRequestKeys } from '../prompt-requests/hooks'
import type { PromptRequestRecord } from '../prompt-requests/types'
import * as api from './api'
import { checkedSource, clarificationContentConflicts, sameQuestions, validClarificationCount, validClarificationQuestions } from './clarificationFollowup'
import { isTerminal, type WorkflowContentHistory, type WorkflowCreateRequest, type WorkflowListParams, type WorkflowMetadataHistory,
  type WorkflowProgress, type WorkflowResult, type WorkflowStartRequest } from './contracts'
import { WorkflowClientError } from './errors'
import { workflowRead } from './readDeadline'
import { trustworthyProgress } from './progress'
import { contentConflicts, historiesConflict, reconcileResult, sameId, trustworthyMetadata } from './readValidation'
import { blockClarificationFollowup, isWorkflowIntegrityError, observeWorkflowRevision, revokeResultIntegrity } from './resultIntegrity'

export const workflowKeys = {
  all: ['workflows'] as const,
  lists: (parentId: string) => ['workflows', 'list', parentId] as const,
  list: (parentId: string, params: WorkflowListParams = {}) =>
    ['workflows', 'list', parentId, params.limit ?? 20, params.before ?? null] as const,
  run: (id: string) => ['workflows', 'run', id] as const,
  progress: (id: string, parentId?: string) => ['workflows', 'run', id, 'progress', parentId ?? null] as const,
  metadata: (id: string, parentId?: string) => ['workflows', 'run', id, 'metadata', parentId ?? null] as const,
  content: (id: string, parentId?: string) => ['workflows', 'run', id, 'content', parentId ?? null] as const,
  result: (id: string, parentId?: string) => ['workflows', 'run', id, 'result', parentId ?? null] as const,
}

function savedSource(client: QueryClient, parentId: string) {
  for (const [key, record] of client.getQueriesData<PromptRequestRecord>({ queryKey: promptRequestKeys.details() })) {
    if (record && typeof key[2] === 'string' && sameId(key[2], parentId)) return checkedSource({ record, queryId: key[2] }, parentId)
  }
}

async function readWithIntegrity<T>(client: QueryClient, id: string, parentId: string | undefined,
  signal: AbortSignal, read: (signal: AbortSignal) => Promise<T>): Promise<T> {
  try {
    return await workflowRead(signal, read)
  } catch (error) {
    if (!signal.aborted && parentId && isWorkflowIntegrityError(error)) revokeResultIntegrity(client, id, parentId)
    throw error
  }
}

// No global observers/timers, focus polling or persisted cache. Future screens opt
// into progress polling while mounted. Signals are consumed by every read function.
const readDefaults = { retry: false, refetchInterval: false, refetchOnWindowFocus: false,
  refetchIntervalInBackground: false, gcTime: 0 } as const
export const workflowProgressQuery = (id: string, parentId?: string) => queryOptions({
  ...readDefaults, queryKey: workflowKeys.progress(id, parentId),
  queryFn: ({ client, signal }) => workflowRead(signal, async bounded => {
    const row = await api.getWorkflowProgress(id, bounded, parentId)
    if (parentId && !bounded.aborted && trustworthyProgress(row) && validClarificationCount(row)) {
      observeWorkflowRevision(client, id, parentId, row.state_revision)
    }
    return row
  }),
})
export const workflowMonitoringQuery = (id: string, parentId: string, enabled: boolean) => queryOptions({
  ...workflowProgressQuery(id, parentId), enabled: query => enabled && query.state.status !== 'error', staleTime: 2000,
  refetchOnMount: false, refetchOnReconnect: false, networkMode: 'always',
  // One observer owns one timer. Wait for each read to finish before scheduling
  // another; the first read error stops automatic traffic until a manual refresh.
  refetchInterval: query => query.state.status === 'error' || query.state.fetchStatus === 'fetching'
    ? false : progressPollingInterval(query.state.data),
})
export const workflowMetadataQuery = (id: string, parentId?: string) => queryOptions({
  ...readDefaults, queryKey: workflowKeys.metadata(id, parentId),
  queryFn: ({ client, signal }) => workflowRead(signal, async bounded => {
    const summary = await api.getWorkflowMetadataHistory(id, bounded, parentId)
    const result = client.getQueryData<WorkflowResult>(workflowKeys.result(id, parentId))
    const content = client.getQueryData<WorkflowContentHistory>(workflowKeys.content(id, parentId))
    if (parentId && !bounded.aborted && trustworthyMetadata(summary) && validClarificationCount(summary.workflow) &&
      !(result && reconcileResult(result, summary) === 'conflict') &&
      !(content && trustworthyMetadata(content.metadata) && historiesConflict(content.metadata, summary))) {
      observeWorkflowRevision(client, id, parentId, summary.workflow.state_revision)
    }
    return summary
  }),
})
export const workflowContentQuery = (id: string, parentId?: string) => queryOptions({
  ...readDefaults, staleTime: Infinity, refetchOnMount: false, refetchOnReconnect: false,
  networkMode: 'always', queryKey: workflowKeys.content(id, parentId),
  queryFn: ({ client, signal }) => readWithIntegrity(client, id, parentId, signal, async bounded => {
    const content = await api.getWorkflowContentHistory(id, bounded, parentId)
    const summary = client.getQueryData<WorkflowMetadataHistory>(workflowKeys.metadata(id, parentId))
    const result = client.getQueryData<WorkflowResult>(workflowKeys.result(id, parentId))
    if (trustworthyMetadata(content.metadata) && contentConflicts(content, summary, result)) {
      throw new WorkflowClientError('malformed_response', 200, 'workflow_integrity_inconsistent')
    }
    if (parentId && !bounded.aborted && trustworthyMetadata(content.metadata)) {
      const row = content.metadata.workflow
      if (row.status === 'CLARIFICATION_REQUIRED') {
        const saved = savedSource(client, parentId)
        if (!validClarificationQuestions(row, content.clarification_questions) ||
          (saved && clarificationContentConflicts(saved, result, content)) ||
          (result?.workflow.state_revision === row.state_revision &&
           !sameQuestions(result.clarification_questions, content.clarification_questions))) {
          blockClarificationFollowup(client, id, parentId)
          return content
        }
      }
      if (validClarificationCount(row) && (row.status === 'CLARIFICATION_REQUIRED' || content.clarification_questions.length === 0)) {
        observeWorkflowRevision(client, id, parentId, row.state_revision)
      }
    }
    return content
  }),
})
export const workflowResultQuery = (id: string, parentId?: string) => queryOptions({
  ...readDefaults, staleTime: Infinity, refetchOnMount: false, refetchOnReconnect: false,
  networkMode: 'always', queryKey: workflowKeys.result(id, parentId),
  queryFn: ({ client, signal }) => readWithIntegrity(client, id, parentId, signal, async bounded => {
    const result = await api.getWorkflowResult(id, bounded, parentId)
    const summary = client.getQueryData<WorkflowMetadataHistory>(workflowKeys.metadata(id, parentId))
    const content = client.getQueryData<WorkflowContentHistory>(workflowKeys.content(id, parentId))
    if ((summary && trustworthyMetadata(summary) && reconcileResult(result, summary) === 'conflict') ||
      (content && trustworthyMetadata(content.metadata) && contentConflicts(content, undefined, result))) {
      throw new WorkflowClientError('malformed_response', 200, 'workflow_integrity_inconsistent')
    }
    if (parentId && !bounded.aborted && content && result.workflow.status === 'CLARIFICATION_REQUIRED' &&
      content.metadata.workflow.state_revision === result.workflow.state_revision &&
      !sameQuestions(result.clarification_questions, content.clarification_questions)) {
      blockClarificationFollowup(client, id, parentId)
      return result
    }
    const valid = result.workflow.status === 'CLARIFICATION_REQUIRED'
      ? validClarificationQuestions(result.workflow, result.clarification_questions)
      : result.workflow.clarification_question_count === 0 && result.clarification_questions.length === 0
    if (parentId && !bounded.aborted && valid) {
      observeWorkflowRevision(client, id, parentId, result.workflow.state_revision)
    }
    return result
  }),
})
export const workflowListQuery = (parentId: string, params: WorkflowListParams = {}) => queryOptions({
  ...readDefaults, queryKey: workflowKeys.list(parentId, params),
  queryFn: ({ signal }) => workflowRead(signal, bounded => api.listWorkflows(parentId, params, bounded)),
})
export function progressPollingInterval(value: WorkflowProgress | undefined): number | false {
  return value && !isTerminal(value.status) && value.history_valid && !value.records_truncated &&
    value.findings.length === 0 ? 2000 : false
}

type CreateVariables = { readonly payload: WorkflowCreateRequest; readonly signal?: AbortSignal }
type StartVariables = { readonly payload: WorkflowStartRequest; readonly signal?: AbortSignal }
export function workflowCreateMutation(client: QueryClient, parentId: string) {
  return mutationOptions({
    mutationKey: ['workflows', 'create', parentId], retry: false, gcTime: 0,
    mutationFn: ({ payload, signal }: CreateVariables) => api.createWorkflow(parentId, payload, signal),
    onSuccess: () => { void client.invalidateQueries({ queryKey: workflowKeys.lists(parentId) }) },
  })
}

// Shared by all hook instances using the existing application QueryClient. This is
// a duplicate-click guard, not admission authority or a queued mutation scope.
const pendingStarts = new WeakMap<QueryClient, Set<string>>()
export function workflowStartMutation(client: QueryClient, id: string) {
  return mutationOptions({
    mutationKey: ['workflows', 'start', id], retry: false, gcTime: 0,
    mutationFn: async ({ payload, signal }: StartVariables) => {
      let pending = pendingStarts.get(client)
      if (!pending) { pending = new Set(); pendingStarts.set(client, pending) }
      if (pending.has(id)) throw new WorkflowClientError('duplicate_start')
      pending.add(id)
      try { return await api.startWorkflow(id, payload, signal) } finally { pending.delete(id) }
    },
    onSuccess: (value) => {
      void client.invalidateQueries({ queryKey: workflowKeys.run(id) })
      void client.invalidateQueries({ queryKey: workflowKeys.lists(value.workflow.prompt_request_id) })
    },
  })
}
