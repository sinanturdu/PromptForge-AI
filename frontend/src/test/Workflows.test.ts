import { afterEach, describe, expect, it, vi } from 'vitest'
import { MutationObserver, QueryObserver } from '@tanstack/react-query'
import { createAppQueryClient } from '../app/queryClient'
import * as api from '../features/workflows/api'
import * as wire from '../features/workflows/contracts'
import { WorkflowClientError } from '../features/workflows/errors'
import { observeWorkflowOutcome } from '../features/workflows/availability'
import * as queries from '../features/workflows/queries'
import { PARENT, RUN, DATE, EVALUATION, VERSION, PRIVATE, progress, metadata, content, result } from './workflowFixtures'

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks() })
function transport(body: unknown, status = 200) {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}
const identity = { id: RUN, prompt_request_id: PARENT, status: 'PENDING' }
const creation = { workflow: identity, replayed: false }
const acceptance = { workflow: identity, accepted: true }

describe('workflow wire client', () => {
  it.each([201, 200])('serializes a stable body key and handles creation %s', async status => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://untrusted.invalid')
    const fetcher = transport({ ...creation, replayed: status === 200 }, status)
    const payload = { client_idempotency_key: 'Action.Key:001', max_evaluation_rounds: 2 as const }
    const before = JSON.stringify(payload)
    expect((await api.createWorkflow(PARENT, payload)).replayed).toBe(status === 200)
    expect(fetcher).toHaveBeenCalledOnce()
    const [url, init] = fetcher.mock.calls[0]!
    expect(url).toBe(`/api/v1/prompt-requests/${PARENT}/runs`)
    expect(init).toMatchObject({ method: 'POST', cache: 'no-store', mode: 'same-origin',
      credentials: 'same-origin', redirect: 'error', body: before })
    expect(init.headers).not.toHaveProperty('Idempotency-Key')
    expect(JSON.stringify(payload)).toBe(before)
  })
  it('treats 202 only as acceptance and never retries', async () => {
    const fetcher = transport(acceptance, 202)
    const value = await api.startWorkflow(RUN, { expected_revision: 0 })
    expect(value.accepted).toBe(true)
    expect(value.workflow.status).toBe('PENDING')
    expect(fetcher).toHaveBeenCalledOnce()
    expect(fetcher.mock.calls[0]![0]).toBe(`/api/v1/runs/${RUN}/execution`)
    expect(fetcher.mock.calls[0]![1].body).toBe('{"expected_revision":0}')
  })
  it('propagates AbortSignal and distinguishes cancellation', async () => {
    const controller = new AbortController()
    const fetcher = transport(progress())
    await api.getWorkflowProgress(RUN, controller.signal)
    expect(fetcher.mock.calls[0]![1].signal).toBe(controller.signal)
    controller.abort()
    fetcher.mockRejectedValue(new DOMException(PRIVATE, 'AbortError'))
    await expect(api.getWorkflowProgress(RUN, controller.signal)).rejects.toMatchObject({ kind: 'cancelled' })
  })
  it('separates metadata, content and result paths and keeps null values', async () => {
    const fetcher = transport(metadata())
    expect((await api.getWorkflowMetadataHistory(RUN)).operations[0]!.telemetry).toBeNull()
    expect(fetcher.mock.calls[0]![0]).toBe(`/api/v1/runs/${RUN}/history`)
    fetcher.mockResolvedValue(new Response(JSON.stringify(content()), { headers: { 'Content-Type': 'application/json' } }))
    expect((await api.getWorkflowContentHistory(RUN)).source!.raw_prompt).toBe(PRIVATE)
    expect(fetcher.mock.calls[1]![0]).toBe(`/api/v1/runs/${RUN}/history/content`)
    fetcher.mockResolvedValue(new Response(JSON.stringify(result()), { headers: { 'Content-Type': 'application/json' } }))
    expect((await api.getWorkflowResult(RUN)).candidate).toBeNull()
    expect(fetcher.mock.calls[2]![0]).toBe(`/api/v1/runs/${RUN}/result`)
  })
  it('serializes paired cursor pagination and parent assertions', async () => {
    const fetcher = transport({ items: [], next_cursor: { created_at: DATE, workflow_run_id: RUN } })
    const response = await api.listWorkflows(PARENT, { limit: 10, before: { created_at: DATE, workflow_run_id: RUN } })
    expect(response.next_cursor!.created_at).toBe(DATE)
    const url = new URL(fetcher.mock.calls[0]![0], 'http://test.invalid')
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: '10', before_created_at: DATE, before_workflow_run_id: RUN })
    fetcher.mockResolvedValue(new Response(JSON.stringify(progress()), { headers: { 'Content-Type': 'application/json' } }))
    await api.getWorkflowProgress(RUN, undefined, PARENT)
    expect(fetcher.mock.calls[1]![0]).toBe(`/api/v1/runs/${RUN}?prompt_request_id=${PARENT}`)
  })
  it.each([403, 404, 409, 422, 429, 503, 500])('handles RFC 9457 status %s without exposing raw bodies', async status => {
    const fetcher = transport({ type: 'about:blank', title: 'Failure', status, detail: PRIVATE,
      instance: '/safe', request_id: RUN, error_code: status === 409 ? 'workflow_conflict' : PRIVATE,
      errors: [{ field: 'raw_prompt', code: PRIVATE, message: PRIVATE }], exception: PRIVATE }, status)
    await expect(api.startWorkflow(RUN, { expected_revision: 0 })).rejects.toMatchObject({ kind: 'api', status, requestId: RUN })
    try { await api.getWorkflowProgress(RUN) } catch (error) {
      expect(JSON.stringify(error)).not.toContain(PRIVATE)
      expect(String(error)).not.toContain(PRIVATE)
    }
    expect(fetcher).toHaveBeenCalledTimes(2) // one per explicit operation; no retry
  })
  it.each(['', '<html>internal secret</html>', '{invalid', 'null', '{}'])('rejects malformed success body %s', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { headers: { 'Content-Type': 'application/json' } })))
    await expect(api.getWorkflowProgress(RUN)).rejects.toMatchObject({ kind: 'malformed_response' })
  })
  it('sanitizes empty/HTML errors and network exceptions', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(PRIVATE, { status: 503 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(api.getWorkflowProgress(RUN)).rejects.toMatchObject({ kind: 'api', status: 503 })
    fetcher.mockRejectedValue(new Error(PRIVATE))
    await expect(api.getWorkflowProgress(RUN)).rejects.toMatchObject({ kind: 'network', status: 0 })
  })
  it('rejects unexpected success status and leaking fields', async () => {
    const fetcher = transport(acceptance, 200)
    await expect(api.startWorkflow(RUN, { expected_revision: 0 })).rejects.toMatchObject({ kind: 'malformed_response' })
    fetcher.mockResolvedValue(new Response(JSON.stringify({ ...metadata(), raw_prompt: PRIVATE }), { headers: { 'Content-Type': 'application/json' } }))
    await expect(api.getWorkflowMetadataHistory(RUN)).rejects.toMatchObject({ kind: 'malformed_response' })
  })
  it('does not log prompts, responses or exceptions', async () => {
    const logs = [vi.spyOn(console, 'log'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'error'), vi.spyOn(console, 'info')]
    transport(content())
    await api.getWorkflowContentHistory(RUN)
    for (const log of logs) expect(log).not.toHaveBeenCalled()
  })
  it('validates identifiers and sends no provider overrides', async () => {
    const fetcher = transport(creation, 201)
    expect(() => api.workflowPath('../private')).toThrow(WorkflowClientError)
    expect(() => api.createWorkflow(PARENT, { client_idempotency_key: '' })).toThrow(WorkflowClientError)
    expect(() => api.startWorkflow(RUN, { expected_revision: -1 })).toThrow(WorkflowClientError)
    await api.createWorkflow(PARENT, { client_idempotency_key: 'stable', provider: PRIVATE } as wire.WorkflowCreateRequest)
    expect(fetcher.mock.calls[0]![1].body).toBe('{"client_idempotency_key":"stable"}')
  })
})

describe('trust, quality and query integration', () => {
  const selected: wire.SelectedEvaluation = { evaluation_id: EVALUATION, prompt_version_id: VERSION,
    evaluation_round: 1, total_score: '0.0000', qualified: false, quality_status: 'not_qualified',
    computed_selection_policy_version: '1.0.0' }
  it('preserves decimal zero, unknown scores and independent quality', () => {
    expect(wire.progressResponse(progress()).stored_final_score).toBeNull()
    const complete = wire.progressResponse(progress({ status: 'COMPLETED', selected, stored_final_score: '0.0000' }))
    expect(wire.trustedSelection(complete)!.quality_status).toBe('not_qualified')
    expect(complete.stored_final_score).toBe('0.0000')
    expect(wire.trustedSelection(progress({ selected: { ...selected, total_score: null, quality_status: 'unassessable' } }))!.quality_status).toBe('unassessable')
    expect(wire.trustedSelection(progress({ selected, findings: [{ code: 'terminal_state', scope: 'terminal', record_id: null }] }))).toBeNull()
    expect(wire.trustedSelection(progress({ selected, history_valid: false }))).toBeNull()
    expect(wire.trustedSelection(progress({ selected, records_truncated: true }))).toBeNull()
  })
  it('does not return an inconsistent or nonterminal result as trusted content', async () => {
    const fetcher = transport({ ...result(), workflow: progress({ findings: [{ code: 'workflow_state', scope: 'workflow', record_id: RUN }] }) })
    await expect(api.getWorkflowResult(RUN)).rejects.toMatchObject({ kind: 'malformed_response' })
    fetcher.mockResolvedValue(new Response(JSON.stringify({ ...result(), workflow: progress() }), { headers: { 'Content-Type': 'application/json' } }))
    await expect(api.getWorkflowResult(RUN)).rejects.toMatchObject({ kind: 'malformed_response' })
  })
  it('has distinct scoped query keys, no active polling and no POST retry', () => {
    const client = createAppQueryClient()
    expect(queries.workflowKeys.metadata(RUN)).not.toEqual(queries.workflowKeys.content(RUN))
    expect(queries.workflowKeys.progress(RUN)).not.toEqual(queries.workflowKeys.progress(RUN, PARENT))
    expect(queries.workflowProgressQuery(RUN).refetchInterval).toBe(false)
    expect(queries.progressPollingInterval(progress())).toBe(2000)
    expect(queries.progressPollingInterval(progress({ status: 'FAILED' }))).toBe(false)
    expect(queries.progressPollingInterval(undefined)).toBe(false)
    expect(queries.workflowCreateMutation(client, PARENT).retry).toBe(false)
    expect(queries.workflowStartMutation(client, RUN).retry).toBe(false)
    client.clear()
  })
  it('aborts an observed query when its last observer leaves', async () => {
    const client = createAppQueryClient()
    let signal: AbortSignal | undefined
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      signal = init.signal ?? undefined
      signal!.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')))
    }))
    vi.stubGlobal('fetch', fetcher)
    const observer = new QueryObserver(client, queries.workflowProgressQuery(RUN))
    const unsubscribe = observer.subscribe(() => {})
    expect(fetcher).toHaveBeenCalledOnce()
    unsubscribe()
    expect(signal!.aborted).toBe(true)
    client.clear()
  })
  it('rejects duplicate starts across mutation instances without queuing', async () => {
    const client = createAppQueryClient()
    let release: ((response: Response) => void) | undefined
    const fetcher = vi.fn(() => new Promise<Response>(resolve => { release = resolve }))
    vi.stubGlobal('fetch', fetcher)
    const first = new MutationObserver(client, queries.workflowStartMutation(client, RUN))
    const second = new MutationObserver(client, queries.workflowStartMutation(client, RUN))
    const pending = first.mutate({ payload: { expected_revision: 0 } })
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce())
    await expect(second.mutate({ payload: { expected_revision: 0 } })).rejects.toMatchObject({ kind: 'duplicate_start' })
    release!(new Response(JSON.stringify(acceptance), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    await pending
    expect(fetcher).toHaveBeenCalledOnce()
    client.clear()
  })
  it('does not retry a failed start under TanStack defaults and invalidates on creation', async () => {
    const client = createAppQueryClient()
    const fetcher = transport({ status: 503, error_code: 'workflow_unavailable' }, 503)
    const mutation = new MutationObserver(client, queries.workflowStartMutation(client, RUN))
    await expect(mutation.mutate({ payload: { expected_revision: 0 } })).rejects.toMatchObject({ status: 503 })
    expect(fetcher).toHaveBeenCalledOnce()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    fetcher.mockResolvedValue(new Response(JSON.stringify(creation), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    await new MutationObserver(client, queries.workflowCreateMutation(client, PARENT)).mutate({ payload: { client_idempotency_key: 'same-action' } })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queries.workflowKeys.lists(PARENT) })
    client.clear()
  })
  it('uses only real outcomes for availability and never probes', () => {
    expect(observeWorkflowOutcome('read', 'success')).toBe('api_observed_execution_unknown')
    expect(observeWorkflowOutcome('create', 'success')).toBe('creation_observed_execution_unknown')
    expect(observeWorkflowOutcome('start', 'success')).toBe('execution_accepted')
    expect(observeWorkflowOutcome('read', new WorkflowClientError('api', 404, 'workflow_not_found'))).toBe('api_observed_execution_unknown')
    expect(observeWorkflowOutcome('read', new WorkflowClientError('api', 404, 'not_found'))).toBe('route_unavailable')
    expect(observeWorkflowOutcome('start', new WorkflowClientError('api', 503))).toBe('execution_unavailable')
    expect(observeWorkflowOutcome('start', new WorkflowClientError('api', 403))).toBe('access_denied')
    expect(observeWorkflowOutcome('read', new WorkflowClientError('network'))).toBe('unknown')
  })
})
