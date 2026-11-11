import { uuid, type WorkflowMetadataHistory } from './contracts'
import { WorkflowClientError } from './errors'

export type RoundLimit = 1 | 2 | 3
export interface WorkflowAttempt {
  readonly version: 1
  readonly key: string
  readonly rounds: RoundLimit
  readonly workflowId: string | null
  readonly start: 'not_submitted' | 'unknown' | 'accepted' | 'rejected'
}

export class AttemptStorageError extends Error {
  constructor() { super('Güvenli deneme kaydı kullanılamıyor. Yeni bir işlem gönderilmedi.') }
}

const storageKey = (parentId: string) => `promptforge.workflow-attempt.v1:${uuid(parentId).toLowerCase()}`

// Only identifiers, immutable round count and a submission marker. Never store
// prompt text, preferences, API responses, scores, credentials or fencing values.
export function readAttempt(parentId: string): WorkflowAttempt | null {
  try {
    const raw = sessionStorage.getItem(storageKey(parentId))
    if (raw === null) return null
    if (raw.length > 512) throw new Error()
    const row: unknown = JSON.parse(raw)
    if (typeof row !== 'object' || row === null || Array.isArray(row)) throw new Error()
    const value = row as Record<string, unknown>
    if (Object.keys(value).sort().join(',') !== 'key,rounds,start,version,workflowId' ||
      value.version !== 1 || typeof value.key !== 'string' || !value.key.startsWith('ui-') ||
      ![1, 2, 3].includes(value.rounds as number) ||
      !['not_submitted', 'unknown', 'accepted', 'rejected'].includes(value.start as string)) throw new Error()
    uuid(value.key.slice(3))
    if (value.workflowId !== null) uuid(value.workflowId)
    if (value.workflowId === null && value.start !== 'not_submitted') throw new Error()
    return value as unknown as WorkflowAttempt
  } catch { throw new AttemptStorageError() }
}

export function writeAttempt(parentId: string, next: WorkflowAttempt | null, previous: WorkflowAttempt | null): void {
  try {
    // A stale component/late receipt cannot overwrite a newer local attempt.
    if (JSON.stringify(readAttempt(parentId)) !== JSON.stringify(previous)) throw new Error()
    const key = storageKey(parentId)
    if (next === null) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, JSON.stringify(next))
    if (JSON.stringify(readAttempt(parentId)) !== JSON.stringify(next)) throw new Error()
  } catch { throw new AttemptStorageError() }
}

export function newAttempt(rounds: RoundLimit): WorkflowAttempt {
  return { version: 1, key: `ui-${crypto.randomUUID()}`, rounds, workflowId: null, start: 'not_submitted' }
}

export function hasTrustedState(value: WorkflowMetadataHistory): boolean {
  return value.workflow.history_valid && !value.workflow.records_truncated &&
    value.workflow.findings.length === 0 && !value.operations.some(v => v.incomplete || v.unresolved)
}
export function canSubmit(value: WorkflowMetadataHistory): boolean {
  const row = value.workflow
  return hasTrustedState(value) && row.status === 'PENDING' && row.state_revision === 0 &&
    row.current_round === 0 && row.completed_rounds === 0 && value.operations.length === 0 &&
    value.versions.length === 0 && value.evaluations.length === 0
}
export function definitiveStartRejection(error: WorkflowClientError): boolean {
  return error.kind === 'api' && [403, 404, 405, 409, 422, 429, 503].includes(error.status) &&
    !error.errorCode.startsWith('http_')
}
