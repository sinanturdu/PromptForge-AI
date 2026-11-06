import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowMetadataHistory } from '../features/workflows/contracts'
import { readAttempt } from '../features/workflows/attempts'
import { getRecordedPostPayloads, seedMockPromptRecord } from './msw/handlers'
import { server } from './msw/server'
import { renderAppAtRoutes } from './renderWithApp'
import { DATE, PARENT, PRIVATE, RUN, metadata, progress } from './workflowFixtures'

const identity = { id: RUN, prompt_request_id: PARENT, status: 'PENDING' }
let creates: Record<string, unknown>[]
let starts: Record<string, unknown>[]
let reads: number
let stored: WorkflowMetadataHistory
let createReply: () => Response | Promise<Response>
let startReply: () => Response | Promise<Response>

function problem(status: number, code: string) {
  return HttpResponse.json({ type: 'about:blank', title: 'Rejected', status, detail: PRIVATE,
    instance: '/safe', request_id: RUN, error_code: code, traceback: PRIVATE },
    { status, headers: { 'Content-Type': 'application/problem+json' } })
}
function gate() {
  let release!: (response: Response) => void
  const promise = new Promise<Response>(resolve => { release = resolve })
  return { promise, release }
}
beforeEach(() => {
  sessionStorage.clear()
  creates = []; starts = []; reads = 0
  stored = { ...metadata(), operations: [] }
  createReply = () => HttpResponse.json({ workflow: identity, replayed: false }, { status: 201 })
  startReply = () => HttpResponse.json({ workflow: identity, accepted: true }, { status: 202 })
  seedMockPromptRecord({ id: PARENT, raw_prompt: PRIVATE, user_context: null, output_language: 'tr',
    target_mode: 'generic', task_profile: null, prompt_detail_level: 'medium',
    response_detail_preference: 'balanced', reasoning_preference: 'auto', created_at: DATE })
  server.use(
    http.post('/api/v1/prompt-requests/:id/runs', async ({ params, request }) => {
      expect(params.id).toBe(PARENT)
      creates.push(await request.json() as Record<string, unknown>)
      return createReply()
    }),
    http.post('/api/v1/runs/:id/execution', async ({ params, request }) => {
      expect(params.id).toBe(RUN)
      starts.push(await request.json() as Record<string, unknown>)
      return startReply()
    }),
    http.get('/api/v1/runs/:id/history', ({ request, params }) => {
      expect(params.id).toBe(RUN)
      expect(new URL(request.url).searchParams.get('prompt_request_id')).toBe(PARENT)
      reads += 1
      return HttpResponse.json(stored)
    }),
  )
})
afterEach(() => { sessionStorage.clear(); vi.restoreAllMocks() })

async function open() {
  const view = renderAppAtRoutes([`/prompt-requests/${PARENT}`])
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Optimizasyonu Yapılandır' }))
  return { ...view, user }
}
async function created() {
  const view = await open()
  await view.user.click(screen.getByRole('button', { name: 'Workflow Oluştur' }))
  await screen.findByText(RUN)
  return view
}

describe('explicit saved prompt optimization', () => {
  it('opens valid bounded configuration without readiness probes', async () => {
    await open()
    const select = screen.getByLabelText('En fazla değerlendirme turu')
    expect(select).toHaveValue('3')
    expect(screen.getAllByRole('option').map(option => (option as HTMLOptionElement).value)).toEqual(['1', '2', '3'])
    expect(creates).toHaveLength(0); expect(starts).toHaveLength(0); expect(reads).toBe(0)
    expect(getRecordedPostPayloads()).toHaveLength(0)
  })
  it('rejects invalid rounds before POST', async () => {
    const { user } = await open()
    fireEvent.change(screen.getByLabelText('En fazla değerlendirme turu'), { target: { value: '9' } })
    await user.click(screen.getByRole('button', { name: 'Workflow Oluştur' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('1, 2 veya 3')
    expect(screen.getByLabelText('En fazla değerlendirme turu')).toHaveAttribute('aria-invalid', 'true')
    expect(creates).toHaveLength(0)
  })
  it('creates for the existing parent with correct body and no implicit start', async () => {
    const { user } = await open()
    await user.selectOptions(screen.getByLabelText('En fazla değerlendirme turu'), '2')
    await user.click(screen.getByRole('button', { name: 'Workflow Oluştur' }))
    await screen.findByText(RUN)
    expect(creates).toEqual([{ client_idempotency_key: expect.stringMatching(/^ui-/), max_evaluation_rounds: 2 }])
    expect(starts).toHaveLength(0); expect(getRecordedPostPayloads()).toHaveLength(0)
    expect(screen.getByText(/Optimizasyon henüz başlatılmadı/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Optimizasyonu Başlat' })).toBeEnabled()
  })
  it('reuses a lost creation key and configuration across unmount/reload and 200 replay', async () => {
    createReply = () => HttpResponse.error()
    const first = await open()
    await first.user.selectOptions(screen.getByLabelText('En fazla değerlendirme turu'), '2')
    await first.user.click(screen.getByRole('button', { name: 'Workflow Oluştur' }))
    await screen.findByRole('alert')
    expect(creates).toHaveLength(1)
    const body = creates[0]
    first.unmount(); first.queryClient.clear()
    createReply = () => HttpResponse.json({ workflow: identity, replayed: true }, { status: 200 })
    const second = renderAppAtRoutes([`/prompt-requests/${PARENT}`])
    const retry = await screen.findByRole('button', { name: 'Workflow Oluşturmayı Tekrar Dene' })
    expect(creates).toHaveLength(1)
    expect(screen.getByLabelText('En fazla değerlendirme turu')).toBeDisabled()
    await userEvent.click(retry)
    await screen.findByText(/Önceden oluşturulan workflow bulundu/)
    expect(creates).toEqual([body, body])
    expect(starts).toHaveLength(0)
    second.queryClient.clear()
  })
  it('locks double clicks and conflicting configuration while creation is pending', async () => {
    const pending = gate()
    createReply = () => pending.promise
    const { user } = await open()
    await user.dblClick(screen.getByRole('button', { name: 'Workflow Oluştur' }))
    await waitFor(() => expect(creates).toHaveLength(1))
    expect(screen.getByLabelText('En fazla değerlendirme turu')).toBeDisabled()
    pending.release(HttpResponse.json({ workflow: identity, replayed: false }, { status: 201 }))
    await screen.findByText(RUN)
    expect(creates).toHaveLength(1)
  })
  it('requires explicit start, reads the required revision, and acknowledges 202 honestly', async () => {
    const { user } = await created()
    expect(starts).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    await screen.findByText('Optimizasyon isteği kabul edildi.')
    expect(starts).toEqual([{ expected_revision: 0 }])
    expect(reads).toBe(1)
    expect(screen.getByText(/tamamlandığı veya kalite koşullarını sağladığı anlamına gelmez/)).toBeInTheDocument()
    expect(screen.queryByText(/Optimizasyon henüz başlatılmadı/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Optimizasyonu Başlat' })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Prompt Geçmişine Git' })).toHaveAttribute('href', '/history')
    await user.click(screen.getByRole('link', { name: 'Prompt Geçmişine Git' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Prompt Geçmişi' })).toBeInTheDocument()
    expect(starts).toHaveLength(1)
  })
  it('prevents duplicate starts while the acknowledgment is pending', async () => {
    const pending = gate()
    startReply = () => pending.promise
    const { user } = await created()
    await user.dblClick(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    await waitFor(() => expect(starts).toHaveLength(1))
    expect(readAttempt(PARENT)?.start).toBe('unknown')
    expect(screen.getByRole('button', { name: /Başlatma kontrol/ })).toBeDisabled()
    pending.release(HttpResponse.json({ workflow: identity, accepted: true }, { status: 202 }))
    await screen.findByText('Optimizasyon isteği kabul edildi.')
    expect(starts).toHaveLength(1)
  })
  it('locks uncertain start across reload, including after a GET still shows PENDING', async () => {
    startReply = () => HttpResponse.error()
    const first = await created()
    await first.user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    await screen.findByText('Başlatma isteğinin sonucu belirsiz.')
    await screen.findByRole('alert')
    first.unmount(); first.queryClient.clear()
    renderAppAtRoutes([`/prompt-requests/${PARENT}`])
    await screen.findByText('Başlatma isteğinin sonucu belirsiz.')
    expect(starts).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Kayıtlı Durumu Kontrol Et' }))
    await screen.findByText(/Son okunan kayıtlı durum: Başlatılmayı bekliyor/)
    expect(screen.getByRole('button', { name: 'Optimizasyonu Başlat' })).toBeDisabled()
    expect(starts).toHaveLength(1)
    expect(creates).toHaveLength(1)
    expect(reads).toBe(2)
  })
  it('does not dispatch after navigation during preflight', async () => {
    const pending = gate()
    server.use(http.get('/api/v1/runs/:id/history', () => pending.promise))
    const { user } = await created()
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    await user.click(screen.getByRole('link', { name: /Yeni Prompt İsteği Oluştur/ }))
    await screen.findByRole('heading', { name: 'Yeni bir prompt oluştur' })
    pending.release(HttpResponse.json(stored))
    expect(starts).toHaveLength(0)
    expect(readAttempt(PARENT)?.start).toBe('not_submitted')
  })
  it('distinguishes failed preflight from an uncertain POST', async () => {
    server.use(http.get('/api/v1/runs/:id/history', () => HttpResponse.error()))
    const { user } = await created()
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('başlatma isteği gönderilmedi')
    expect(screen.queryByText('Başlatma isteğinin sonucu belirsiz.')).not.toBeInTheDocument()
    expect(starts).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Optimizasyonu Başlat' })).toBeEnabled()
  })
  it.each(['GENERATING', 'COMPLETED', 'FAILED', 'CLARIFICATION_REQUIRED'] as const)('shows persisted %s without restarting', async status => {
    stored = { ...stored, workflow: progress({ status }) }
    const { user } = await created()
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Başlatma yapılmadı')
    expect(await screen.findByText(/Son okunan kayıtlı durum:/)).toBeInTheDocument()
    expect(starts).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Optimizasyonu Başlat' })).toBeDisabled()
  })
  it('blocks unresolved/inconsistent history instead of attempting recovery', async () => {
    stored = { ...metadata(), workflow: progress({ findings: [{ code: 'workflow_state', scope: 'workflow', record_id: RUN }] }) }
    const { user } = await created()
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    await screen.findByText(/Kaydın bütünlüğü veya önceki işlem sonucu incelenmeli/)
    expect(starts).toHaveLength(0)
    expect(screen.queryByRole('button', { name: 'Yeni Workflow Yapılandır' })).not.toBeInTheDocument()
  })
  it.each([[403, 'workflow_access_denied'], [404, 'not_found'], [405, 'method_not_allowed'],
    [409, 'workflow_conflict'], [422, 'validation_error'], [429, 'workflow_capacity_exceeded'],
    [503, 'workflow_execution_environment_unsafe'], [500, 'internal_server_error']] as const)(
    'sanitizes create HTTP %s while preserving the prompt', async (status, code) => {
      createReply = () => problem(status, code)
      const { user } = await open()
      await user.click(screen.getByRole('button', { name: 'Workflow Oluştur' }))
      const alert = await screen.findByRole('alert')
      expect(alert).not.toHaveTextContent(PRIVATE)
      expect(screen.getByText(PRIVATE)).toBeInTheDocument() // only the existing raw prompt view
      expect(creates).toHaveLength(1); expect(starts).toHaveLength(0)
      expect(screen.getByRole('link', { name: /Prompt Geçmişine Dön/ })).toBeInTheDocument()
      if (status === 404 || status === 405) expect(alert).toHaveTextContent('kapalı veya yalnızca okumaya açık')
    },
  )
  it('allows explicit retry after known capacity rejection, never automatically', async () => {
    startReply = () => problem(429, 'workflow_capacity_exceeded')
    const { user } = await created()
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Yeni işlem kuyruğa alınmadı')
    expect(readAttempt(PARENT)?.start).toBe('rejected')
    expect(starts).toHaveLength(1)
    startReply = () => HttpResponse.json({ workflow: identity, accepted: true }, { status: 202 })
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    await screen.findByText('Optimizasyon isteği kabul edildi.')
    expect(starts).toHaveLength(2)
    expect(reads).toBe(2)
  })
  it('preserves start uncertainty after HTTP 500', async () => {
    startReply = () => problem(500, 'internal_server_error')
    const { user } = await created()
    await user.click(screen.getByRole('button', { name: 'Optimizasyonu Başlat' }))
    await screen.findByText('Başlatma isteğinin sonucu belirsiz.')
    expect(readAttempt(PARENT)?.start).toBe('unknown')
    expect(starts).toHaveLength(1)
  })
  it('only rotates the key after explicit new configuration for a trusted terminal workflow', async () => {
    const { user } = await created()
    const firstKey = creates[0]!.client_idempotency_key
    stored = { ...stored, workflow: progress({ status: 'COMPLETED' }) }
    await user.click(screen.getByRole('button', { name: 'Kayıtlı Durumu Kontrol Et' }))
    await user.click(await screen.findByRole('button', { name: 'Yeni Workflow Yapılandır' }))
    expect(creates).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Workflow Oluştur' }))
    await screen.findByText(RUN)
    expect(creates).toHaveLength(2)
    expect(creates[1]!.client_idempotency_key).not.toBe(firstKey)
    expect(getRecordedPostPayloads()).toHaveLength(0)
  })
  it('fails closed if session storage cannot protect an attempt', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error(PRIVATE) })
    const { user } = await open()
    await user.click(screen.getByRole('button', { name: 'Workflow Oluştur' }))
    expect(await screen.findByText('Güvenli deneme kaydı kullanılamıyor')).toBeInTheDocument()
    expect(creates).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Workflow Oluştur' })).toBeDisabled()
  })
  it('never stores or logs prompt content and does not expose internal controls', async () => {
    const logs = [vi.spyOn(console, 'log'), vi.spyOn(console, 'error'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'info')]
    await created()
    const state = Object.values(sessionStorage).join('')
    expect(state).not.toContain(PRIVATE)
    expect(Object.keys(readAttempt(PARENT)!)).toEqual(['version', 'key', 'rounds', 'workflowId', 'start'])
    for (const log of logs) expect(log).not.toHaveBeenCalled()
    expect(screen.queryByLabelText(/token|provider|revision/i)).not.toBeInTheDocument()
  })
  it('saving on the original page never creates or starts a workflow', async () => {
    renderAppAtRoutes(['/'])
    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/Ham Prompt İsteği/), 'Save only, with all existing preferences.')
    await user.click(screen.getByRole('button', { name: 'Prompt İsteğini Kaydet' }))
    await screen.findByRole('heading', { level: 1, name: 'Prompt İsteği Detayları' })
    expect(getRecordedPostPayloads()).toHaveLength(1)
    expect(creates).toHaveLength(0); expect(starts).toHaveLength(0); expect(reads).toBe(0)
  })
})
