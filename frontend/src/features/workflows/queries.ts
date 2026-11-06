import { mutationOptions, queryOptions, type QueryClient } from '@tanstack/react-query'
import * as api from './api'
import { isTerminal, type WorkflowCreateRequest, type WorkflowListParams, type WorkflowProgress,
  type WorkflowStartRequest } from './contracts'
import { WorkflowClientError } from './errors'
import { workflowRead } from './readDeadline'

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

// No global observers/timers, focus polling or persisted cache. Future screens opt
// into progress polling while mounted. Signals are consumed by every read function.
const readDefaults = { retry: false, refetchInterval: false, refetchOnWindowFocus: false,
  refetchIntervalInBackground: false, gcTime: 0 } as const
export const workflowProgressQuery = (id: string, parentId?: string) => queryOptions({
  ...readDefaults, queryKey: workflowKeys.progress(id, parentId),
  queryFn: ({ signal }) => workflowRead(signal, bounded => api.getWorkflowProgress(id, bounded, parentId)),
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
  queryFn: ({ signal }) => workflowRead(signal, bounded => api.getWorkflowMetadataHistory(id, bounded, parentId)),
})
export const workflowContentQuery = (id: string, parentId?: string) => queryOptions({
  ...readDefaults, queryKey: workflowKeys.content(id, parentId),
  queryFn: ({ signal }) => api.getWorkflowContentHistory(id, signal, parentId),
})
export const workflowResultQuery = (id: string, parentId?: string) => queryOptions({
  ...readDefaults, queryKey: workflowKeys.result(id, parentId),
  queryFn: ({ signal }) => api.getWorkflowResult(id, signal, parentId),
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
