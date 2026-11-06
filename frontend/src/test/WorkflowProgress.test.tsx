import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '../app/providers'
import { WorkflowMonitor } from '../features/workflows/components/WorkflowMonitor'
import { createTestQueryClient } from './renderWithApp'
import { DATE, PARENT, PRIVATE, RUN, metadata, progress } from './workflowFixtures'
import type { WorkflowProgress } from '../features/workflows/contracts'

const OTHER = '90000000-0000-0000-0000-000000000009'
let row: WorkflowProgress
let fetcher: ReturnType<typeof vi.fn>
const clients: ReturnType<typeof createTestQueryClient>[] = []
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': status < 400 ? 'application/json' : 'application/problem+json' },
})
async function tick(ms = 1) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
function view(id: string | null = RUN) {
  const client = createTestQueryClient()
  clients.push(client)
  return render(<AppProviders queryClient={client}><MemoryRouter initialEntries={[`/prompt-requests/${PARENT}${id ? `?workflow=${id}` : ''}`]}>
    <WorkflowMonitor parentId={PARENT} followDefault={false} />
  </MemoryRouter></AppProviders>)
}
beforeEach(() => {
  vi.useFakeTimers()
  row = progress({ status: 'GENERATING', current_round: 1 })
  fetcher = vi.fn().mockImplementation(() => Promise.resolve(json(row)))
  vi.stubGlobal('fetch', fetcher)
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
  vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks()
})

describe('visible persisted workflow monitoring', () => {
  it('reads a scoped relative URL and polls once per 2 seconds without POST', async () => {
    view(); await tick()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0]![0]).toBe(`/api/v1/runs/${RUN}?prompt_request_id=${PARENT}`)
    expect(screen.getByText(/Bağlı prompt isteği/)).toBeInTheDocument()
    await tick(1998); expect(fetcher).toHaveBeenCalledTimes(1)
    await tick(3); expect(fetcher).toHaveBeenCalledTimes(2)
    await tick(2001); expect(fetcher).toHaveBeenCalledTimes(3)
    expect(fetcher.mock.calls.every(([, options]) => options.method === 'GET' && options.signal instanceof AbortSignal)).toBe(true)
  })
  it.each(['PENDING', 'GENERATING', 'EVALUATING', 'REVISING'] as const)('displays durable %s without invented percentage or provider liveness', async status => {
    row = progress({ status, current_round: status === 'PENDING' ? 0 : 1 })
    view(); await tick()
    expect(screen.getByText(/Son okunan kayıtlı durum:/)).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument()
    expect(screen.getByText(/Son doğrulanmış değerlendirme puanı: Bilinmiyor/)).toBeInTheDocument()
    if (status === 'PENDING') expect(screen.getByText(/PENDING bir kuyruk veya yürütme garantisi değildir/)).toBeInTheDocument()
  })
  it.each(['COMPLETED', 'FAILED', 'CLARIFICATION_REQUIRED'] as const)('stops on %s including early completion', async status => {
    row = progress({ status, current_round: 1, completed_rounds: 1, max_evaluation_rounds: 3 })
    view(); await tick(); await tick(20_000)
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/Son okunan kayıtlı durum:/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'İzlemeyi Duraklat' })).not.toBeInTheDocument()
    if (status === 'COMPLETED') expect(screen.getByText(/promptun kalite koşullarını sağladığı anlamına gelmez/)).toBeInTheDocument()
    if (status === 'CLARIFICATION_REQUIRED') expect(screen.getByText(/ek kullanıcı açıklaması gerekiyor/)).toBeInTheDocument()
  })
  it('does not claim safe completion for inconsistent history', async () => {
    row = progress({ status: 'COMPLETED', findings: [{ code: 'terminal_state', scope: 'terminal', record_id: RUN }] })
    view(); await tick(); await tick(6000)
    expect(screen.getByText('Kaydın bütünlüğü veya önceki işlem sonucu incelenmeli.')).toBeInTheDocument()
    expect(screen.queryByText(/Son okunan kayıtlı durum: Yürütme tamamlandı/)).not.toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('never overlaps reads and aborts an outstanding read on unmount', async () => {
    let signal: AbortSignal | undefined
    fetcher.mockImplementation((_url, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
      signal = options.signal!
      signal!.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')))
    }))
    const rendered = view(); await tick(6000)
    expect(fetcher).toHaveBeenCalledTimes(1)
    rendered.unmount(); await tick()
    expect(signal!.aborted).toBe(true)
    await tick(10_000); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('pauses in a hidden tab and resumes with one GET rather than a focus burst', async () => {
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    view(); await tick()
    visibility.mockReturnValue('hidden')
    fireEvent(document, new Event('visibilitychange'))
    await tick(8000); expect(fetcher).toHaveBeenCalledTimes(1)
    visibility.mockReturnValue('visible')
    fireEvent(document, new Event('visibilitychange'))
    fireEvent(window, new Event('focus'))
    await tick(); expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('bounds a stalled GET without a permanent loading state or automatic retry', async () => {
    fetcher.mockImplementation((_url, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
      options.signal!.addEventListener('abort', () => reject(new DOMException('Deadline', 'AbortError')))
    }))
    view(); await tick(10_001)
    expect(screen.getByRole('alert')).toHaveTextContent('İlerleme yenilenemedi')
    expect(screen.getByRole('button', { name: 'Şimdi Yenile' })).toBeEnabled()
    await tick(30_000); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('withholds completion when on-demand history exposes an unresolved terminal operation', async () => {
    row = progress({ status: 'COMPLETED' })
    fetcher.mockImplementation((url: string) => Promise.resolve(json(url.includes('/history')
      ? { ...metadata(), workflow: row } : row)))
    view(); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'İşlem Özetini Görüntüle' })); await tick()
    expect(screen.queryByText(/Son okunan kayıtlı durum: Yürütme tamamlandı/)).not.toBeInTheDocument()
    expect(screen.getByText('Kaydın bütünlüğü veya önceki işlem sonucu incelenmeli.')).toBeInTheDocument()
  })
  it('pauses when the panel is offscreen', async () => {
    let notify!: IntersectionObserverCallback
    const disconnect = vi.fn()
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: IntersectionObserverCallback) { notify = callback }
      observe() {}
      disconnect() { disconnect() }
    })
    view(); await tick(); expect(fetcher).not.toHaveBeenCalled()
    act(() => notify([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
    await tick(); expect(fetcher).toHaveBeenCalledTimes(1)
    act(() => notify([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver))
    await tick(6000); expect(fetcher).toHaveBeenCalledTimes(1)
    cleanup(); expect(disconnect).toHaveBeenCalled()
  })
  it('closes observation entirely and allows GET-only manual pause/refresh', async () => {
    view(); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'İzlemeyi Duraklat' }))
    await tick(5000); expect(fetcher).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Şimdi Yenile' }))
    await tick(); expect(fetcher).toHaveBeenCalledTimes(2)
    await tick(5000); expect(fetcher).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole('button', { name: 'İzlemeyi Sürdür' }))
    await tick(); expect(fetcher).toHaveBeenCalledTimes(3)
    fireEvent.click(screen.getByRole('button', { name: 'İzlemeyi Kapat' }))
    await tick(6000); expect(fetcher).toHaveBeenCalledTimes(3)
  })
  it('keeps stale persisted state, stops on network error, and resumes only after manual success', async () => {
    view(); await tick()
    fetcher.mockRejectedValue(new Error(PRIVATE))
    await tick(2001)
    expect(screen.getByRole('alert')).toHaveTextContent('İlerleme yenilenemedi')
    expect(screen.getByText(/Son okunan kayıtlı durum: Prompt hazırlanıyor/)).toBeInTheDocument()
    expect(screen.queryByText(/Son okunan kayıtlı durum: İşlem başarısız/)).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).not.toHaveTextContent(PRIVATE)
    await tick(30_000); expect(fetcher).toHaveBeenCalledTimes(2)
    fireEvent(document, new Event('visibilitychange')); await tick(6000)
    expect(fetcher).toHaveBeenCalledTimes(2)
    fetcher.mockImplementation(() => Promise.resolve(json(row)))
    fireEvent.click(screen.getByRole('button', { name: 'Şimdi Yenile' }))
    await tick(); await tick(2001)
    expect(fetcher).toHaveBeenCalledTimes(4)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it.each([403, 404, 409, 422, 429, 503, 500])('sanitizes HTTP %s and bounds further traffic', async status => {
    fetcher.mockResolvedValue(json({ status, detail: PRIVATE, error_code: 'internal_server_error', request_id: RUN }, status))
    view(); await tick(); await tick(20_000)
    expect(screen.getByRole('alert')).not.toHaveTextContent(PRIVATE)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('rejects malformed or wrong-parent responses', async () => {
    fetcher.mockResolvedValue(json({ ...row, prompt_request_id: OTHER }))
    view(); await tick()
    expect(screen.getByRole('alert')).toHaveTextContent('geçersiz iş akışı yanıtı')
    expect(screen.queryByText(/Son okunan kayıtlı durum:/)).not.toBeInTheDocument()
  })
  it('suppresses misleading cancellation alerts', async () => {
    fetcher.mockRejectedValue(new DOMException('Cancelled', 'AbortError'))
    view(); await tick()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('announces no changes for identical snapshots while refresh time changes outside the live region', async () => {
    view(); await tick()
    const region = screen.getAllByRole('status').find(element => element.hasAttribute('aria-atomic'))!
    const changes = vi.fn()
    const observer = new MutationObserver(changes)
    observer.observe(region, { childList: true, subtree: true, characterData: true })
    await tick(4002)
    expect(changes).not.toHaveBeenCalled()
    observer.disconnect()
  })
  it('fetches metadata only on request, never content or result, without logging private errors', async () => {
    const logs = [vi.spyOn(console, 'log'), vi.spyOn(console, 'error'), vi.spyOn(console, 'warn')]
    fetcher.mockImplementation((url: string) => Promise.resolve(json(url.includes('/history') ? metadata() : row)))
    view(); await tick()
    expect(fetcher.mock.calls.every(([url]) => !String(url).includes('/history'))).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'İşlem Özetini Görüntüle' }))
    await tick()
    expect(screen.getByText(/Architect · Hazırlama/)).toBeInTheDocument()
    expect(screen.getByText(/Canlı sağlayıcı çağrısı veya yeniden başlatma izni anlamına gelmez/)).toBeInTheDocument()
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/history/content') || String(url).includes('/result') || String(url).includes('/recovery'))).toBe(false)
    for (const log of logs) expect(log).not.toHaveBeenCalled()
  })
  it.each([null, 'invalid', RUN + '&workflow=' + OTHER])('does not read absent/invalid/ambiguous identity %s', async id => {
    view(id); await tick(10_000)
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('selects only scoped workflows with bounded cursor pages and cancels stale selection', async () => {
    let oldSignal: AbortSignal | undefined
    let release!: (response: Response) => void
    let nextPage = false
    fetcher.mockImplementation((url: string, options: RequestInit) => {
      if (url.includes('/prompt-requests/')) {
        nextPage = url.includes('before_created_at=')
        const id = nextPage ? OTHER : RUN
        return Promise.resolve(json({ items: [{ id, prompt_request_id: PARENT, status: 'GENERATING',
          created_at: DATE, finished_at: null, current_round: 1, completed_rounds: 0, integrity_checked: false }],
          next_cursor: nextPage ? null : { created_at: DATE, workflow_run_id: RUN } }))
      }
      if (url.includes(OTHER)) return Promise.resolve(json(progress({ id: OTHER, status: 'FAILED' })))
      oldSignal = options.signal!
      return new Promise<Response>(resolve => { release = resolve })
    })
    view(); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' })); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Sonraki Workflow Sayfası' })); await tick()
    expect(nextPage).toBe(true)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: OTHER } }); await tick()
    expect(oldSignal!.aborted).toBe(true)
    expect(screen.getByText(OTHER)).toBeInTheDocument()
    expect(screen.getByText(/Son okunan kayıtlı durum: İşlem başarısız/)).toBeInTheDocument()
    release(json(progress({ status: 'COMPLETED' }))); await tick(); await tick(6000)
    expect(screen.queryByText(/Son okunan kayıtlı durum: Yürütme tamamlandı/)).not.toBeInTheDocument()
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes(`/runs/${RUN}`))).toHaveLength(1)
    expect(fetcher.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
  })
})
