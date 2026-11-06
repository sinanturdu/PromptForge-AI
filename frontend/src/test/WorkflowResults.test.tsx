import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryObserver } from '@tanstack/react-query'
import { AppProviders } from '../app/providers'
import { WorkflowMonitor } from '../features/workflows/components/WorkflowMonitor'
import { criterionLabels } from '../features/workflows/evaluationLabels'
import { workflowContentQuery, workflowKeys, workflowResultQuery } from '../features/workflows/queries'
import { readResultIntegrity, revokeResultIntegrity } from '../features/workflows/resultIntegrity'
import { createTestQueryClient } from './renderWithApp'
import { DATE, EVALUATION, PARENT, PRIVATE, RUN, VERSION } from './workflowFixtures'
import { CREATOR, CRITIC, ORIGINAL, OTHER_RUN, RENDERED, resultFixture } from './workflowResultFixtures'

let fixture: ReturnType<typeof resultFixture>
let fetcher: ReturnType<typeof vi.fn>
let clipboard: ReturnType<typeof vi.fn>
const clients: ReturnType<typeof createTestQueryClient>[] = []
const retainedObservers: (() => void)[] = []
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': status < 400 ? 'application/json' : 'application/problem+json' },
})
async function tick(ms = 1) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
function view(url = `?workflow=${RUN}&view=result`, client = createTestQueryClient()) {
  if (!clients.includes(client)) clients.push(client)
  return render(<AppProviders queryClient={client}><MemoryRouter initialEntries={[`/prompt-requests/${PARENT}${url}`]}>
    <WorkflowMonitor parentId={PARENT} followDefault={false} />
  </MemoryRouter></AppProviders>)
}
function retainResult(client: ReturnType<typeof createTestQueryClient>, id = RUN) {
  const observer = new QueryObserver(client, { ...workflowResultQuery(id, PARENT), enabled: false, gcTime: Infinity })
  retainedObservers.push(observer.subscribe(() => {}))
}
function inconsistentHistory(failure: 'findings' | 'http_integrity') {
  return failure === 'http_integrity' ? json({ status: 409, error_code: 'workflow_integrity_inconsistent' }, 409)
    : json({ ...fixture.history, metadata: { ...fixture.history.metadata,
      workflow: { ...fixture.history.metadata.workflow, selected: null, stored_best_version_id: null,
        findings: [{ code: 'progress_selection', scope: 'workflow', record_id: RUN }] } } })
}
function otherWorkflow() {
  let serialized = JSON.stringify(resultFixture())
  for (const [from, to] of [[RUN, OTHER_RUN], [VERSION, '80000000-0000-0000-0000-000000000008'],
    [EVALUATION, '90000000-0000-0000-0000-000000000009'], [CREATOR, 'a0000000-0000-0000-0000-00000000000a'],
    [CRITIC, 'b0000000-0000-0000-0000-00000000000b']] as const) serialized = serialized.replaceAll(from, to)
  return JSON.parse(serialized) as ReturnType<typeof resultFixture>
}
function answer(url: string) {
  return url.includes('/history/content') ? fixture.history : url.includes('/history') ? fixture.metadata
    : url.includes('/result') ? fixture.result : fixture.result.workflow
}
beforeEach(() => {
  vi.useFakeTimers()
  fixture = resultFixture()
  fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(json(answer(url))))
  vi.stubGlobal('fetch', fetcher)
  clipboard = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText: clipboard }, onLine: true, language: 'tr-TR' })
})
afterEach(() => {
  cleanup(); retainedObservers.splice(0).forEach(unsubscribe => unsubscribe()); clients.splice(0).forEach(client => client.clear())
  vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks()
})

describe('persisted results, quality and privacy', () => {
  it.each(['findings', 'http_integrity'] as const)('retains revocation across result and parent remounts with cached approval: %s', async failure => {
    const rendered = view(); await tick()
    const client = clients.at(-1)!
    retainResult(client)
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/history/content') ? inconsistentHistory(failure) : json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Kapat' })); await tick()
    expect(client.getQueryData(workflowKeys.result(RUN, PARENT))).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' })); await tick()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    rendered.unmount(); await tick()
    view(`?workflow=${RUN}&view=result`, client); await tick()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/result'))).toHaveLength(1)
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true)
  })
  it.each(['findings', 'http_integrity'] as const)('isolates revocation when switching to a valid workflow and returning: %s', async failure => {
    view(); await tick()
    const client = clients.at(-1)!
    retainResult(client)
    const other = otherWorkflow()
    fetcher.mockImplementation((url: string) => {
      if (url.includes('/prompt-requests/')) return Promise.resolve(json({ items: [fixture, other].map(value => ({
        id: value.result.workflow.id, prompt_request_id: PARENT, status: 'COMPLETED', created_at: DATE,
        finished_at: DATE, current_round: 1, completed_rounds: 1, integrity_checked: false })), next_cursor: null }))
      if (url.includes(`/runs/${OTHER_RUN}`)) return Promise.resolve(json(url.includes('/history') ? other.metadata : url.includes('/result') ? other.result : other.result.workflow))
      return Promise.resolve(url.includes('/history/content') ? inconsistentHistory(failure) : json(answer(url)))
    })
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' })); await tick()
    fireEvent.change(screen.getByLabelText('Bu promptun workflow’ları'), { target: { value: OTHER_RUN } }); await tick(); await tick()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick()
    expect(readResultIntegrity(client, RUN, PARENT).revoked).toBe(true)
    expect(readResultIntegrity(client, OTHER_RUN, PARENT).revoked).toBe(false)
    fireEvent.change(screen.getByLabelText('Bu promptun workflow’ları'), { target: { value: RUN } }); await tick(); await tick()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes(`/runs/${RUN}/result`))).toHaveLength(1)
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true)
  })
  it.each(['pending', 'cancelled', 'timeout', 'failed', 'invalid'] as const)('keeps remounted approval revoked during a %s read and unrelated successful GETs', async outcome => {
    view(); await tick()
    const client = clients.at(-1)!
    retainResult(client)
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/history/content') ? inconsistentHistory('findings') : json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Kapat' })); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' })); await tick()
    let complete!: (response: Response) => void
    fetcher.mockImplementation((url: string, init: RequestInit) => {
      if (!url.includes('/result')) return Promise.resolve(json(answer(url)))
      if (outcome === 'failed') return Promise.resolve(json({ status: 503, error_code: 'workflow_unavailable' }, 503))
      if (outcome === 'invalid') return Promise.resolve(json({ ...fixture.result, workflow: { ...fixture.result.workflow, prompt_request_id: OTHER_RUN } }))
      return new Promise<Response>((resolve, reject) => {
        complete = resolve
        init.signal!.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')))
      })
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick()
    if (outcome === 'cancelled') await act(async () => { await client.cancelQueries({ queryKey: workflowKeys.result(RUN, PARENT) }) })
    if (outcome === 'timeout') await tick(10_001)
    fireEvent.click(screen.getByRole('button', { name: 'Şimdi Yenile' })); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş Özetini Yenile' })); await tick()
    expect(readResultIntegrity(client, RUN, PARENT).revoked).toBe(true)
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    if (outcome === 'pending') {
      expect(screen.getByRole('button', { name: 'Sonucu Yenile' })).toBeDisabled()
      complete(json(fixture.result)); await tick()
    } else {
      fetcher.mockImplementation((url: string) => Promise.resolve(json(answer(url))))
      fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick()
    }
    await tick()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true)
  })
  it('discards retained inconsistent content after fresh result verification and requires a new explicit content load', async () => {
    view(); await tick()
    const client = clients.at(-1)!
    const contentKey = workflowKeys.content(RUN, PARENT)
    const observer = new QueryObserver(client, { ...workflowContentQuery(RUN, PARENT), enabled: false, gcTime: Infinity })
    retainedObservers.push(observer.subscribe(() => {}))
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/history/content') ? inconsistentHistory('findings') : json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(client.getQueryData(contentKey)).toBeDefined()
    fetcher.mockImplementation((url: string) => Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick(); await tick()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    expect(client.getQueryData(contentKey)).toBeUndefined()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/history/content'))).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/history/content'))).toHaveLength(2)
    expect(screen.getByLabelText('Sol prompt metni').textContent).toBe(ORIGINAL)
  })
  it('does not let an older pending revalidation erase a newer integrity observation', async () => {
    view(); await tick()
    const client = clients.at(-1)!
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/history/content') ? inconsistentHistory('findings') : json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    let complete!: (response: Response) => void
    fetcher.mockImplementation((url: string) => url.includes('/result') ? new Promise<Response>(resolve => { complete = resolve }) : Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick()
    act(() => revokeResultIntegrity(client, RUN, PARENT))
    complete(json(fixture.result)); await tick()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    expect(readResultIntegrity(client, RUN, PARENT).revoked).toBe(true)
  })
  it('uses both canonical UUID identities without transferring revocation to another parent', () => {
    const client = createTestQueryClient(); clients.push(client)
    const mixed = 'ABCDEF00-0000-0000-0000-000000000001'
    revokeResultIntegrity(client, mixed, PARENT)
    expect(readResultIntegrity(client, mixed.toLowerCase(), PARENT).revoked).toBe(true)
    expect(readResultIntegrity(client, mixed, OTHER_RUN).revoked).toBe(false)
    expect(readResultIntegrity(client, OTHER_RUN, PARENT).revoked).toBe(false)
  })
  it('has no result UI or requests without selection or explicit result viewing', async () => {
    const rendered = view(''); await tick()
    expect(screen.queryByRole('region', { name: 'Sonuç ve Değerlendirme Geçmişi' })).not.toBeInTheDocument()
    expect(fetcher).not.toHaveBeenCalled(); rendered.unmount()
    view(`?workflow=${RUN}`); await tick()
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/result'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' })); await tick()
    expect(screen.getByRole('region', { name: 'Seçilen prompt' })).toBeInTheDocument()
  })
  it.each(['PENDING', 'GENERATING', 'EVALUATING', 'REVISING'] as const)('does not fetch a terminal result for %s', async status => {
    fixture.result = { ...fixture.result, workflow: { ...fixture.result.workflow, status } }
    fixture.metadata = { ...fixture.metadata, workflow: fixture.result.workflow }
    view(); await tick()
    expect(screen.getByText(/Nihai sonuç henüz hazır değil/)).toBeInTheDocument()
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/result'))).toBe(false)
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
  })
  it('retrieves a completed result once, preserves exact text and scopes every GET', async () => {
    view(); await tick(); await tick(20_000)
    expect(screen.getByLabelText('Seçilen prompt metni').textContent).toBe(RENDERED)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/result'))).toHaveLength(1)
    expect(fetcher.mock.calls.every(([url, init]) => String(url).startsWith('/api/v1/') &&
      String(url).includes(`prompt_request_id=${PARENT}`) && init.method === 'GET' && init.cache === 'no-store')).toBe(true)
    fireEvent(window, new Event('focus')); await tick(6000)
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/result'))).toHaveLength(1)
    expect(screen.getByText(/üst sınıra ulaşmadan sonlandı/)).toBeInTheDocument()
  })
  it.each(['qualified', 'not_qualified', 'unassessable'] as const)('uses backend quality %s independently of completion', async quality => {
    fixture = resultFixture(quality, quality === 'unassessable' ? null : '92.123400')
    view(); await tick()
    const panel = screen.getByRole('region', { name: 'Sonuç ve Değerlendirme Geçmişi' })
    expect(within(panel).getByText('Yürütme tamamlandı; sonuç bütünlüğü doğrulandı.')).toBeInTheDocument()
    expect(within(panel).getByText(/Kalite sonucu:/)).toHaveTextContent(quality === 'qualified' ? 'Kalite koşulları sağlandı' : quality === 'not_qualified' ? 'Kalite koşulları sağlanmadı' : 'Değerlendirilemedi')
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    if (quality === 'unassessable') {
      expect(screen.getByLabelText('Açıklık puanı')).toHaveTextContent('Değerlendirilemedi')
      expect(within(panel).getByText(/Backend tarafından seçilen değerlendirmenin puanı:/)).toHaveTextContent('Değerlendirilemedi')
    } else expect(within(panel).getByText(/Backend tarafından seçilen değerlendirmenin puanı:/)).toHaveTextContent('92.123400')
  })
  it('renders a real zero and all nine criterion names without number coercion', async () => {
    fixture = resultFixture('not_qualified', '0.000000')
    fixture.result = { ...fixture.result, assessment: { ...fixture.result.assessment!, criteria: { ...fixture.result.assessment!.criteria,
      intent_fidelity: { score: 0, rationale: 'Kayıtlı sıfır.', evidence: [] } } } }
    view(); await tick()
    expect(screen.getByLabelText('Niyete sadakat puanı')).toHaveTextContent('0 / 10')
    for (const label of Object.values(criterionLabels)) expect(screen.getByLabelText(`${label} puanı`)).toBeInTheDocument()
    expect(screen.getByText('Toplam puan: 0.000000')).toBeInTheDocument()
  })
  it('shows supplied issues, uncertainty, recommendations and evidence as plain data', async () => {
    fixture.result = { ...fixture.result, assessment: { ...fixture.result.assessment!, issues: [{ criterion: 'clarity', code: 'contradiction',
      status: 'uncertain', severity: null, description: '<script>private issue</script>', recommendation: 'Kayıtlı öneri.',
      evidence: [{ source: 'rendered_prompt', quote: 'Merhaba' }] }] } }
    view(); await tick()
    expect(screen.getByText(/Belirsiz yargı; önem derecesi atanmadı/)).toBeInTheDocument()
    expect(screen.getByText('<script>private issue</script>')).toBeInTheDocument()
    expect(screen.getByText('Öneri: Kayıtlı öneri.')).toBeInTheDocument()
    expect(screen.getByText('Aday prompt: Merhaba')).toBeInTheDocument()
  })
  it('copies only the exact selected text, never notes/evidence, and announces success', async () => {
    view(); await tick()
    fireEvent.click(screen.getByRole('button', { name: "Prompt'u Kopyala" })); await tick()
    expect(clipboard).toHaveBeenCalledExactlyOnceWith(RENDERED)
    expect(screen.getByText('Prompt kopyalandı.')).toHaveAttribute('role', 'status')
  })
  it('reports clipboard rejection without leaking the private exception', async () => {
    clipboard.mockRejectedValue(new Error(PRIVATE))
    view(); await tick(); fireEvent.click(screen.getByRole('button', { name: "Prompt'u Kopyala" })); await tick()
    expect(screen.getByText(/Prompt kopyalanamadı/)).not.toHaveTextContent(PRIVATE)
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  })
  it.each(['FAILED', 'CLARIFICATION_REQUIRED'] as const)('never invents a successful best prompt for %s', async status => {
    fixture.result = { ...fixture.result, workflow: { ...fixture.result.workflow, status }, clarification_questions: status === 'CLARIFICATION_REQUIRED' ? ['Hangi çıktı gerekiyor?'] : [] }
    fixture.metadata = { ...fixture.metadata, workflow: fixture.result.workflow }
    view(); await tick()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    if (status === 'CLARIFICATION_REQUIRED') expect(screen.getByText('Hangi çıktı gerekiyor?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })).toBeEnabled()
  })
  it('keeps missing selected content missing', async () => {
    fixture.result = { ...fixture.result, workflow: { ...fixture.result.workflow, selected: null, stored_best_version_id: null }, candidate: null, evaluation: null, assessment: null }
    fixture.metadata = { ...fixture.metadata, workflow: fixture.result.workflow }
    view(); await tick()
    expect(screen.getByText(/API seçilmiş bir prompt sağlamadı/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
  })
  it('fetches metadata independently and full history only after explicit action, preserving comparison whitespace', async () => {
    view(); await tick()
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/history?'))).toBe(true)
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/history/content'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(screen.getByLabelText('Sol prompt metni').textContent).toBe(ORIGINAL)
    expect(screen.getByLabelText('Sağ prompt metni').textContent).toBe(RENDERED)
    expect(screen.getByText(/Seçilen metinler farklı/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Sol prompt', { exact: true }), { target: { value: VERSION } })
    expect(screen.getByText('Seçilen metinler birebir aynı.')).toBeInTheDocument()
    expect(screen.getByLabelText('Sol prompt metni').textContent).toBe(RENDERED)
    await tick(8000)
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/history/content'))).toHaveLength(1)
  })
  it('labels unfinished metadata without inventing a completed round', async () => {
    fixture.result = { ...fixture.result, workflow: { ...fixture.result.workflow, status: 'EVALUATING', completed_rounds: 0 } }
    fixture.metadata = { ...fixture.metadata, workflow: fixture.result.workflow, evaluations: [],
      operations: fixture.metadata.operations.map(v => ({ ...v, status: 'RUNNING', incomplete: true, unresolved: true, finished_at: null })) }
    view(); await tick()
    expect(screen.getByText('Henüz kayıtlı tamamlanmış değerlendirme yok.')).toBeInTheDocument()
    expect(screen.getByText(/Bunlar tamamlanmış değerlendirme turu sayılmaz/)).toBeInTheDocument()
    expect(screen.queryByText(/Tur 2 · Kayıtlı değerlendirme/)).not.toBeInTheDocument()
  })
  it('refreshes an older active metadata snapshot once when a verified terminal result arrives', async () => {
    const earlier = { ...fixture.metadata, workflow: { ...fixture.result.workflow, status: 'GENERATING', state_revision: 1 },
      evaluations: [], operations: [], versions: [] }
    let reads = 0
    fetcher.mockImplementation((url: string) => Promise.resolve(json(url.includes('/history?')
      ? ++reads === 1 ? earlier : fixture.metadata : answer(url))))
    view(); await tick()
    expect(reads).toBe(2)
    await tick() // Flush the notification from the second, terminal metadata read.
    expect(screen.getByText('Tur 1 · Kayıtlı değerlendirme')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    await tick(20_000); expect(reads).toBe(2)
  })
  it('does not loop when the single terminal metadata refresh fails', async () => {
    const earlier = { ...fixture.metadata, workflow: { ...fixture.result.workflow, status: 'GENERATING', state_revision: 1 },
      evaluations: [], operations: [], versions: [] }
    let reads = 0
    fetcher.mockImplementation((url: string) => {
      if (!url.includes('/history?')) return Promise.resolve(json(answer(url)))
      return ++reads === 1 ? Promise.resolve(json(earlier)) : Promise.reject(new Error(PRIVATE))
    })
    view(); await tick(); await tick(); await tick(20_000)
    expect(reads).toBe(2)
    expect(screen.getByText('Geçmiş özeti okunamadı')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    fetcher.mockImplementation((url: string) => Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş Özetini Yenile' })); await tick()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  })
  it('withholds content and copying on integrity findings while retaining actual metadata', async () => {
    fixture.metadata = { ...fixture.metadata, workflow: { ...fixture.metadata.workflow, findings: [{ code: 'terminal_state', scope: 'terminal', record_id: RUN }] } }
    view(); await tick()
    expect(screen.getByText(/Bu geçmiş doğrulanmış tamamlanma veya kalite kanıtı değildir/)).toBeInTheDocument()
    expect(screen.getByText('Tur 1 · Kayıtlı değerlendirme (doğrulanmadı)')).toBeInTheDocument()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })).toBeDisabled()
  })
  it.each(['findings', 'http_integrity'] as const)('revokes a cached result when content history reports %s', async failure => {
    view(); await tick()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
    const inconsistent = { ...fixture.history, metadata: { ...fixture.history.metadata,
      workflow: { ...fixture.history.metadata.workflow, selected: null, stored_best_version_id: null,
        findings: [{ code: 'progress_selection', scope: 'workflow', record_id: RUN }] } } }
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/history/content')
      ? failure === 'findings' ? json(inconsistent) : json({ status: 409, error_code: 'workflow_integrity_inconsistent' }, 409)
      : json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(screen.getByText('Tur 1 · Kayıtlı değerlendirme')).toBeInTheDocument()
    expect(clipboard).not.toHaveBeenCalled()
    await tick(20_000)
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    // TanStack can resolve cancellation with its older, reverted success data.
    // That is not a fresh validated response and must not restore approval.
    fetcher.mockImplementation((url: string, init: RequestInit) => url.includes('/result')
      ? new Promise<Response>((_resolve, reject) => init.signal!.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError'))))
      : Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick()
    await act(async () => { await clients.at(-1)!.cancelQueries({ queryKey: workflowKeys.result(RUN, PARENT) }) }); await tick()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    // A failed fresh result GET cannot clear the observation after child unmount.
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/result')
      ? json({ status: 409, error_code: 'workflow_integrity_inconsistent' }, 409) : json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    // Only an explicitly requested, newly validated terminal result restores trust.
    fixture.history = { ...fixture.history, metadata: fixture.metadata }
    fetcher.mockImplementation((url: string) => Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick(); await tick()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  })
  it.each([403, 404, 409, 422, 429, 503, 500])('bounds result HTTP %s without executing work or exposing private errors', async status => {
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/result')
      ? json({ status, error_code: status === 409 ? 'workflow_not_terminal' : 'internal_server_error', detail: PRIVATE }, status) : json(answer(url))))
    view(); await tick(); await tick(20_000)
    expect(screen.getByRole('alert')).not.toHaveTextContent(PRIVATE)
    if (status === 409) expect(screen.getByRole('alert')).toHaveTextContent('Sonuç henüz hazır değil')
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/result'))).toHaveLength(1)
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true)
    fetcher.mockImplementation((url: string) => Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' })); await tick()
    expect(screen.getByLabelText('Seçilen prompt metni').textContent).toBe(RENDERED)
  })
  it.each(['parent', 'version', 'criterion', 'integrity'] as const)('rejects an invalid %s result rather than filling missing data', async fault => {
    if (fault === 'parent') fixture.result = { ...fixture.result, workflow: { ...fixture.result.workflow, prompt_request_id: OTHER_RUN } }
    if (fault === 'version') fixture.result = { ...fixture.result, candidate: { ...fixture.result.candidate!, version: { ...fixture.result.candidate!.version, workflow_run_id: OTHER_RUN } } }
    if (fault === 'integrity') fixture.result = { ...fixture.result, workflow: { ...fixture.result.workflow, history_valid: false } }
    const response: unknown = fault === 'criterion' ? { ...fixture.result, assessment: { ...fixture.result.assessment,
      criteria: Object.fromEntries(Object.entries(fixture.result.assessment!.criteria).filter(([key]) => key !== 'clarity')) } } : fixture.result
    // The independent progress/metadata reads are sound; the result response is rejected.
    fetcher.mockImplementation((url: string) => Promise.resolve(json(url.includes('/result') ? response
      : url.includes('/history') ? fixture.metadata : fixture.metadata.workflow)))
    view(); await tick()
    expect(screen.getByRole('alert')).toHaveTextContent('geçersiz iş akışı yanıtı')
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(clipboard).not.toHaveBeenCalled()
  })
  it('displays an integrity-specific 409 safely', async () => {
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/result')
      ? json({ status: 409, error_code: 'workflow_integrity_inconsistent', detail: PRIVATE }, 409) : json(answer(url))))
    view(); await tick()
    expect(screen.getByRole('alert')).toHaveTextContent('Kayıtların bütünlüğü doğrulanamadı')
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
  })
  it('rejects cross-workflow content and does not show a fabricated comparison', async () => {
    fixture.history = { ...fixture.history, versions: [{ ...fixture.history.versions[0]!, version: { ...fixture.history.versions[0]!.version, workflow_run_id: OTHER_RUN } }] }
    view(); await tick(); fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(screen.getByRole('alert')).toHaveTextContent('Geçmiş içeriği okunamadı')
    expect(screen.queryByLabelText('Sol prompt metni')).not.toBeInTheDocument()
  })
  it('preserves metadata during a read interruption but withholds copying until verification recovers', async () => {
    view(); await tick()
    fetcher.mockImplementation((url: string) => url.includes('/history?') ? Promise.reject(new Error(PRIVATE)) : Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş Özetini Yenile' })); await tick()
    expect(screen.getByText('Tur 1 · Kayıtlı değerlendirme')).toBeInTheDocument()
    expect(screen.getByText('Kayıtlı yürütme: Yürütme tamamlandı.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Prompt'u Kopyala" })).not.toBeInTheDocument()
    expect(screen.getByText('Geçmiş özeti okunamadı')).toBeInTheDocument()
    fetcher.mockImplementation((url: string) => Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş Özetini Yenile' })); await tick()
    expect(screen.getByRole('button', { name: "Prompt'u Kopyala" })).toBeEnabled()
  })
  it('rejects malformed JSON history, bounds traffic and allows a read-only manual retry', async () => {
    fetcher.mockImplementation((url: string) => Promise.resolve(url.includes('/history/content')
      ? new Response('{invalid', { headers: { 'Content-Type': 'application/json' } }) : json(answer(url))))
    view(); await tick(); fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick(20_000)
    expect(screen.getByRole('alert')).toHaveTextContent('Geçmiş içeriği okunamadı')
    expect(screen.queryByLabelText('Sol prompt metni')).not.toBeInTheDocument()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/history/content'))).toHaveLength(1)
    fetcher.mockImplementation((url: string) => Promise.resolve(json(answer(url))))
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş İçeriğini Yenile' })); await tick()
    expect(screen.getByLabelText('Sol prompt metni').textContent).toBe(ORIGINAL)
  })
  it('keeps missing historical versions absent instead of reconstructing from canonical fields', async () => {
    fixture.history = { ...fixture.history, source: null, versions: [], evaluations: [] }
    view(); await tick(); fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(screen.getByText('Karşılaştırma için iki doğrulanmış metin bulunamadı.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Sol prompt metni')).not.toBeInTheDocument()
  })
  it('rejects an assessment attached to the wrong persisted evaluation round', async () => {
    fixture.history = { ...fixture.history, metadata: { ...fixture.history.metadata,
      evaluations: fixture.history.metadata.evaluations.map(v => ({ ...v, evaluation_round: 2 })) } }
    view(); await tick(); fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    expect(screen.getByRole('alert')).toHaveTextContent('Geçmiş içeriği okunamadı')
    expect(screen.queryByRole('region', { name: 'Geçmiş Critic değerlendirmeleri' })).not.toBeInTheDocument()
  })
  it('cancels a content read when closed, without leaking a late response or a false failure', async () => {
    let signal!: AbortSignal; let release!: (response: Response) => void
    fetcher.mockImplementation((url: string, init: RequestInit) => {
      if (url.includes('/history/content')) { signal = init.signal!; return new Promise<Response>(resolve => { release = resolve }) }
      return Promise.resolve(json(answer(url)))
    })
    view(); await tick(); fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş İçeriğini Kapat' })); await tick()
    expect(signal.aborted).toBe(true)
    release(json(fixture.history)); await tick()
    expect(screen.queryByLabelText('Sol prompt metni')).not.toBeInTheDocument()
    expect(screen.queryByText('Geçmiş içeriği okunamadı')).not.toBeInTheDocument()
  })
  it('cancels an old result and isolates content when selecting another workflow', async () => {
    let signal!: AbortSignal; let release!: (response: Response) => void
    fetcher.mockImplementation((url: string, init: RequestInit) => {
      if (url.includes('/prompt-requests/')) return Promise.resolve(json({ items: [RUN, OTHER_RUN].map(id => ({ id, prompt_request_id: PARENT,
        status: 'COMPLETED', created_at: DATE, finished_at: DATE, current_round: 1, completed_rounds: 1, integrity_checked: false })), next_cursor: null }))
      if (url.includes(`/runs/${OTHER_RUN}`)) return Promise.resolve(json({ ...fixture.result.workflow, id: OTHER_RUN, status: 'GENERATING' }))
      if (url.includes('/result')) { signal = init.signal!; return new Promise<Response>(resolve => { release = resolve }) }
      return Promise.resolve(json(answer(url)))
    })
    view(); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' })); await tick()
    fireEvent.change(screen.getByLabelText('Bu promptun workflow’ları'), { target: { value: OTHER_RUN } }); await tick()
    expect(signal.aborted).toBe(true)
    release(json(fixture.result)); await tick()
    expect(screen.queryByLabelText('Seçilen prompt metni')).not.toBeInTheDocument()
    expect(screen.queryByText(RENDERED)).not.toBeInTheDocument()
    expect(screen.queryByText('Sonuç okunamadı')).not.toBeInTheDocument()
  })
  it('bounds a stalled result read, suppresses cancellation alerts and preserves workflow status', async () => {
    fetcher.mockImplementation((url: string, init: RequestInit) => url.includes('/result')
      ? new Promise<Response>((_resolve, reject) => init.signal!.addEventListener('abort', () => reject(new DOMException(PRIVATE, 'AbortError'))))
      : Promise.resolve(json(answer(url))))
    const rendered = view(); await tick(10_001)
    expect(screen.getByRole('alert')).toHaveTextContent('Sonuç okunamadı')
    expect(screen.getByText('Kayıtlı yürütme: Yürütme tamamlandı.')).toBeInTheDocument()
    await tick(30_000); expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/result'))).toHaveLength(1)
    rendered.unmount()
    fetcher.mockImplementation((url: string) => url.includes('/result') ? Promise.reject(new DOMException(PRIVATE, 'AbortError')) : Promise.resolve(json(answer(url))))
    view(); await tick(); expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('never logs or persists content while viewing, comparing and copying', async () => {
    const logs = [vi.spyOn(console, 'log'), vi.spyOn(console, 'error'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'info')]
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    view(); await tick(); fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' })); await tick()
    fireEvent.click(screen.getByRole('button', { name: "Prompt'u Kopyala" })); await tick()
    for (const log of logs) expect(log).not.toHaveBeenCalled()
    expect(storage).not.toHaveBeenCalled()
  })
})
