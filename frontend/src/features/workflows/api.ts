import * as wire from './contracts'
import { apiFailure, WorkflowClientError } from './errors'

function id(value: string): string {
  try { return wire.uuid(value) } catch { throw new WorkflowClientError('invalid_request') }
}
export const workflowPath = (workflowId: string) => `/api/v1/runs/${id(workflowId)}`
const parentPath = (parentId: string) => `/api/v1/prompt-requests/${id(parentId)}/runs`
const scoped = (path: string, parentId?: string) => parentId
  ? `${path}?prompt_request_id=${encodeURIComponent(id(parentId))}` : path

async function request<T>(path: string, parse: (body: unknown) => T, statuses: readonly number[],
  signal?: AbortSignal, body?: object): Promise<T> {
  let response: Response
  let data: unknown
  try {
    response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST', mode: 'same-origin', credentials: 'same-origin',
      redirect: 'error', cache: 'no-store', signal,
      headers: { Accept: 'application/json, application/problem+json',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    throw new WorkflowClientError(signal?.aborted || (error instanceof DOMException && error.name === 'AbortError') ? 'cancelled' : 'network')
  }
  try {
    const type = response.headers.get('Content-Type')?.split(';')[0]?.trim()
    if (type !== 'application/json' && type !== 'application/problem+json') throw new Error()
    data = await response.json()
  } catch {
    if (signal?.aborted) throw new WorkflowClientError('cancelled')
    if (!response.ok) throw apiFailure(response.status, null, response.headers.get('X-Request-ID'))
    throw new WorkflowClientError('malformed_response', response.status)
  }
  if (!response.ok) throw apiFailure(response.status, data, response.headers.get('X-Request-ID'))
  if (signal?.aborted) throw new WorkflowClientError('cancelled')
  try {
    if (!statuses.includes(response.status)) throw new Error()
    return parse(data)
  } catch {
    throw new WorkflowClientError('malformed_response', response.status)
  }
}

export function createWorkflow(parentId: string, payload: wire.WorkflowCreateRequest, signal?: AbortSignal) {
  const key = payload.client_idempotency_key
  if (typeof key !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(key) ||
    (payload.max_evaluation_rounds !== undefined && ![1, 2, 3].includes(payload.max_evaluation_rounds))) {
    throw new WorkflowClientError('invalid_request')
  }
  return request(parentPath(parentId), wire.createResponse, [200, 201], signal, {
    client_idempotency_key: key,
    ...(payload.max_evaluation_rounds === undefined ? {} : { max_evaluation_rounds: payload.max_evaluation_rounds }),
  })
}
export function startWorkflow(workflowId: string, payload: wire.WorkflowStartRequest, signal?: AbortSignal) {
  if (!Number.isSafeInteger(payload.expected_revision) || payload.expected_revision < 0) {
    throw new WorkflowClientError('invalid_request')
  }
  return request(`${workflowPath(workflowId)}/execution`, wire.startResponse, [202], signal,
    { expected_revision: payload.expected_revision })
}
export const getWorkflowProgress = (workflowId: string, signal?: AbortSignal, parentId?: string) =>
  request(scoped(workflowPath(workflowId), parentId), wire.progressResponse, [200], signal)
export const getWorkflowMetadataHistory = (workflowId: string, signal?: AbortSignal, parentId?: string) =>
  request(scoped(`${workflowPath(workflowId)}/history`, parentId), wire.metadataResponse, [200], signal)
export const getWorkflowContentHistory = (workflowId: string, signal?: AbortSignal, parentId?: string) =>
  request(scoped(`${workflowPath(workflowId)}/history/content`, parentId), wire.contentResponse, [200], signal)
export const getWorkflowResult = (workflowId: string, signal?: AbortSignal, parentId?: string) =>
  request(scoped(`${workflowPath(workflowId)}/result`, parentId), value => {
    const result = wire.resultResponse(value)
    if (!wire.isTerminal(result.workflow.status) || !result.workflow.history_valid ||
      result.workflow.records_truncated || result.workflow.findings.length > 0) throw new Error()
    const selected = result.workflow.selected
    if (selected && (result.candidate?.version.id !== selected.prompt_version_id ||
      result.evaluation?.id !== selected.evaluation_id || !result.assessment)) throw new Error()
    return result
  }, [200], signal)
export function listWorkflows(parentId: string, params: wire.WorkflowListParams = {}, signal?: AbortSignal) {
  const query = new URLSearchParams()
  if (params.limit !== undefined) {
    if (!Number.isInteger(params.limit) || params.limit < 1 || params.limit > 50) throw new WorkflowClientError('invalid_request')
    query.set('limit', String(params.limit))
  }
  if (params.before) {
    query.set('before_created_at', params.before.created_at)
    query.set('before_workflow_run_id', id(params.before.workflow_run_id))
  }
  return request(`${parentPath(parentId)}${query.size ? `?${query}` : ''}`, wire.listResponse, [200], signal)
}
