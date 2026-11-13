import { useCallback, useMemo, useSyncExternalStore } from 'react'
import { hashKey, skipToken, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { WorkflowClientError } from './errors'

const integrityKey = (workflowId: string, parentId: string) =>
  ['workflows', 'run', workflowId.toLowerCase(), 'integrity', parentId.toLowerCase()] as const

export function isWorkflowIntegrityError(error: unknown): boolean {
  return error instanceof WorkflowClientError && error.errorCode === 'workflow_integrity_inconsistent'
}

interface ResultIntegrity {
  readonly revoked: boolean
  readonly revision: number
}
const EMPTY: ResultIntegrity = Object.freeze({ revoked: false, revision: 0 })

// Only identifiers (in the key) and this tiny record survive view unmounts. No
// prompt/evidence content, browser storage, query function or network timer.
export function readResultIntegrity(client: QueryClient, workflowId: string, parentId: string): ResultIntegrity {
  return client.getQueryData<ResultIntegrity>(integrityKey(workflowId, parentId)) ?? EMPTY
}
export function revokeResultIntegrity(client: QueryClient, workflowId: string, parentId: string): void {
  const key = integrityKey(workflowId, parentId)
  client.setQueryDefaults(key, { gcTime: Infinity, staleTime: Infinity, enabled: false, queryFn: skipToken })
  client.setQueryData<ResultIntegrity>(key, previous => ({ revoked: true, revision: (previous?.revision ?? 0) + 1 }))
}
// Called only after the caller confirms a new validated result GET success. A
// newer integrity observation must also defeat an already-pending revalidation.
export function confirmResultIntegrity(client: QueryClient, workflowId: string, parentId: string, observed: ResultIntegrity): void {
  if (readResultIntegrity(client, workflowId, parentId) !== observed) return
  client.setQueryData<ResultIntegrity>(integrityKey(workflowId, parentId), {
    revoked: false, revision: observed.revision,
  })
}
export function useResultIntegrity(workflowId: string, parentId: string): ResultIntegrity {
  const client = useQueryClient()
  const key = useMemo(() => integrityKey(workflowId, parentId), [workflowId, parentId])
  const hash = useMemo(() => hashKey(key), [key])
  const subscribe = useCallback((listener: () => void) => client.getQueryCache().subscribe(event => {
    if (event.query.queryHash === hash) listener()
  }), [client, hash])
  const snapshot = useCallback(() => readResultIntegrity(client, workflowId, parentId), [client, workflowId, parentId])
  // QueryCache events are synchronous: a layout-time revocation withdraws parent
  // approval before paint, without waiting for a batched QueryObserver notification.
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}
