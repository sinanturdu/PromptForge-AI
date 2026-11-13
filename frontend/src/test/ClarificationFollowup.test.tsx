import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '../app/providers'
import { createPromptRequest } from '../features/prompt-requests/api'
import { usePromptRequestQuery } from '../features/prompt-requests/hooks'
import { applyTextareaEdit } from '../features/prompt-requests/textareaEdits'
import { isBlank } from '../features/prompt-requests/validation'
import { WorkflowMonitor } from '../features/workflows/components/WorkflowMonitor'
import { workflowKeys } from '../features/workflows/queries'
import { observeWorkflowRevision, readResultIntegrity, revokeResultIntegrity } from '../features/workflows/resultIntegrity'
import type { WorkflowStatus } from '../features/workflows/contracts'
import { createTestQueryClient } from './renderWithApp'
import { content, DATE, PARENT, progress, result, RUN } from './workflowFixtures'
import { ORIGINAL, OTHER_RUN, resultFixture } from './workflowResultFixtures'

const NEW_ID = '80000000-0000-0000-0000-000000000008'
const PRIVATE = 'Özel Deniz 🌊\n  <img src=x onerror=alert(1)>'
const QUESTIONS = ['Hangi çıktı?\n  [Markdown]', 'Nasıl ölçelim?', 'ç'.repeat(1000)]
const PREPARE = 'Yanıtlarımla Yeni İstek Taslağı Hazırla'
const SAVE = 'Prompt İsteğini Kaydet'
const SOURCE = { ...content().source!, raw_prompt: ORIGINAL.replaceAll('\n', '\r\n'), user_context: '  Kayıtlı bağlam\n',
  output_language: 'de-DE', target_mode: 'openai', task_profile: 'code_review', prompt_detail_level: 'high',
  response_detail_preference: 'minimal', reasoning_preference: 'low' } as const
const json = (value: unknown, status = 200) => Response.json(value, { status })

function Harness({ missing = false }: { readonly missing?: boolean }) {
  const source = usePromptRequestQuery(PARENT)
  const location = useLocation()
  const navigate = useNavigate()
  return <><output aria-label="Test location">{JSON.stringify([location.pathname, location.search, location.state])}</output>
    <button onClick={() => { void navigate(`/prompt-requests/${OTHER_RUN}`) }}>Open another parent</button>
    {location.pathname.endsWith(PARENT) ? <WorkflowMonitor parentId={PARENT} followDefault={false}
      source={!missing && source.data ? { record: source.data, queryId: PARENT, checking: source.isFetching } : undefined} /> : null}
  </>
}

function view(options: {
  questions?: readonly unknown[]; count?: number; status?: WorkflowStatus; badParent?: boolean
  source?: typeof SOURCE | Record<string, unknown>; missing?: boolean
} = {}) {
  const questions = options.questions ?? QUESTIONS.slice(0, 1)
  const status = options.status ?? 'CLARIFICATION_REQUIRED'
  const record = options.source ?? SOURCE
  const posts: { path: string; payload: Record<string, unknown> }[] = []
  const sourceReads = vi.fn()
  const responses = (id: string) => {
    const row = progress({ id, status, state_revision: 5, started_at: DATE, finished_at: DATE,
      clarification_question_count: options.count ?? questions.length })
    const summary = { workflow: row, versions: [], evaluations: [], operations: [] }
    return {
      row, summary, details: { metadata: summary, source: record, versions: [], evaluations: [], clarification_questions: questions },
      terminal: { ...result(), workflow: options.badParent ? { ...row, prompt_request_id: OTHER_RUN } : row, clarification_questions: questions },
    }
  }
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url, 'http://localhost').pathname
    if (init.method === 'POST') {
      const payload = JSON.parse(String(init.body))
      posts.push({ path, payload })
      return json({ ...payload, id: NEW_ID, created_at: DATE }, 201)
    }
    if (path === `/api/v1/prompt-requests/${PARENT}`) {
      sourceReads()
      return json(record)
    }
    if (path.endsWith('/runs')) return json({ items: [RUN, OTHER_RUN].map(id => ({
      id, prompt_request_id: PARENT, status, created_at: DATE, finished_at: DATE,
      current_round: 1, completed_rounds: 0, integrity_checked: false,
    })), next_cursor: null })
    const id = path.includes(OTHER_RUN) ? OTHER_RUN : RUN
    const values = responses(id)
    if (path.endsWith('/history/content')) return json(values.details)
    if (path.endsWith('/history')) return json(status === 'COMPLETED' ? resultFixture().metadata : values.summary)
    if (path.endsWith('/result')) return json(status === 'COMPLETED' ? resultFixture().result : values.terminal)
    return json(status === 'COMPLETED' ? resultFixture().result.workflow : values.row)
  })
  vi.stubGlobal('fetch', fetcher)
  const queryClient = createTestQueryClient()
  const rendered = render(<AppProviders queryClient={queryClient}>
    <MemoryRouter initialEntries={[`/prompt-requests/${PARENT}?workflow=${RUN}&view=result`]}>
      <Harness missing={options.missing} />
    </MemoryRouter>
  </AppProviders>)
  return { ...rendered, queryClient, posts, sourceReads, fetcher, responses }
}

async function prepare(answers: readonly string[] = [PRIVATE]) {
  const button = await screen.findByRole('button', { name: PREPARE })
  answers.forEach((answer, index) => fireEvent.change(screen.getByRole('textbox', { name: `Yanıt ${index + 1}` }), { target: { value: answer } }))
  fireEvent.click(button)
}

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

describe('clarification follow-up', () => {
  it('tracks only a monotonic scoped revision without overwriting revocation', () => {
    const client = createTestQueryClient()
    observeWorkflowRevision(client, RUN, PARENT, 6)
    const first = readResultIntegrity(client, RUN, PARENT)
    observeWorkflowRevision(client, RUN, PARENT, 5)
    observeWorkflowRevision(client, RUN, PARENT, 6)
    expect(readResultIntegrity(client, RUN, PARENT)).toBe(first)
    expect(readResultIntegrity(client, OTHER_RUN, PARENT).observedRevision).toBeUndefined()
    expect(readResultIntegrity(client, RUN, OTHER_RUN).observedRevision).toBeUndefined()
    revokeResultIntegrity(client, RUN, PARENT)
    observeWorkflowRevision(client, RUN, PARENT, 7)
    expect(readResultIntegrity(client, RUN, PARENT)).toMatchObject({ observedRevision: 7, revoked: true })
    client.clear()
  })

  it('uses a relative same-origin request with redirect rejection only for follow-up', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://example.invalid')
    const fetcher = vi.fn(async () => json({ id: NEW_ID }, 201))
    vi.stubGlobal('fetch', fetcher)
    const payload = { raw_prompt: SOURCE.raw_prompt, user_context: SOURCE.user_context,
      output_language: SOURCE.output_language, target_mode: SOURCE.target_mode, task_profile: SOURCE.task_profile,
      prompt_detail_level: SOURCE.prompt_detail_level, response_detail_preference: SOURCE.response_detail_preference,
      reasoning_preference: SOURCE.reasoning_preference }
    await createPromptRequest(payload, true)
    await createPromptRequest(payload)
    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/v1/prompt-requests', expect.objectContaining({
      mode: 'same-origin', credentials: 'same-origin', redirect: 'error', method: 'POST',
    }))
    expect(fetcher).toHaveBeenNthCalledWith(2, 'https://example.invalid/api/v1/prompt-requests',
      expect.not.objectContaining({ mode: 'same-origin', redirect: 'error' }))
    const base = document.createElement('base')
    base.href = 'https://example.invalid/'
    document.head.append(base)
    try {
      await expect(createPromptRequest(payload, true)).rejects.toMatchObject({ errorCode: 'invalid_request_origin' })
    } finally {
      base.remove()
    }
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it.each(['\u0085', '\u001c', '\u202f', '\r\n'])('treats Python whitespace %j as blank', value => {
    expect(isBlank(value)).toBe(true)
    expect(isBlank(value + '\ufeff')).toBe(false)
  })

  it.each([
    ['a\r\na\na', 'a\na', 0, { start: 0, end: 2 }, 'a\na'],
    ['a\r\na\na', 'a\na', 2, { start: 2, end: 4 }, 'a\r\na'],
    ['a\r\na\na', 'aa\na', 1, { start: 2, end: 2 }, 'aa\na'],
    ['a\r\nb', 'a\n\nb', 3, { start: 2, end: 2 }, 'a\r\n\nb'],
    ['a\r\nb\rc', 'a\nb\nc!', 6, undefined, 'a\r\nb\rc!'],
    ['a\nb', 'a\nç🌊', 5, { start: 2, end: 3 }, 'a\nç🌊'],
  ] as const)('maps a textarea edit to exact original spans (%#)', (original, next, caret, selection, expected) => {
    expect(applyTextareaEdit(original, next, caret, selection)).toBe(expected)
  })

  it('requires a meaningful answer and preserves the exact question, raw prompt, context and preferences', async () => {
    const { posts, sourceReads, queryClient, fetcher } = view()
    const answer = await screen.findByRole('textbox', { name: 'Yanıt 1' })
    const question = document.getElementById(answer.getAttribute('aria-describedby')!)
    expect(question?.textContent).toBe(QUESTIONS[0])
    fireEvent.change(answer, { target: { value: ' \n  ' } })
    fireEvent.click(screen.getByRole('button', { name: PREPARE }))
    expect(answer).toHaveFocus()
    expect(answer).toHaveAccessibleDescription(/Yanıt alanı boş bırakılamaz/)
    expect(posts).toEqual([])
    await prepare()
    expect(screen.getByRole('heading', { name: 'Yeni Prompt İsteği Taslağı' })).toHaveFocus()
    const form = screen.getByRole('form', { name: 'Prompt gönderim formu' }) as HTMLFormElement
    const editable = within(form)
    expect(editable.getByLabelText(/Ham Prompt İsteği/)).toHaveValue(ORIGINAL)
    const composed = `${SOURCE.user_context}\n\nEk Açıklama Yanıtları\n\nSoru 1: ${QUESTIONS[0]}\nYanıt 1: ${PRIVATE}`
    expect(editable.getByLabelText(/Proje Bağlamı/)).toHaveValue(composed)
    expect(editable.getByLabelText(/Çıktı Dili/)).toHaveValue('de-DE')
    expect(editable.getByRole('radio', { name: 'ChatGPT / GPT' })).toBeChecked()
    expect([...new FormData(form)].filter(([name]) => ['task_profile', 'prompt_detail_level', 'response_detail_preference', 'reasoning_preference'].includes(name)))
      .toEqual([['task_profile', 'code_review'], ['prompt_detail_level', 'high'], ['response_detail_preference', 'minimal'], ['reasoning_preference', 'low']])
    fireEvent.change(editable.getByLabelText(/Proje Bağlamı/), { target: { value: composed + '\n  İsteğe bağlı ek.' } })
    expect(document.querySelector('img[src=x]')).toBeNull()
    expect(posts).toEqual([])
    expect(sourceReads).toHaveBeenCalledTimes(1)
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map(query => query.state.data))).not.toContain('Özel Deniz')
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain('Özel Deniz')
    let finish!: (response: Response) => void
    fetcher.mockImplementationOnce(async (url: string, init: RequestInit) => {
      posts.push({ path: new URL(url, 'http://localhost').pathname, payload: JSON.parse(String(init.body)) })
      return new Promise(resolve => { finish = resolve })
    })
    fireEvent.submit(form)
    fireEvent.submit(form)
    await waitFor(() => expect(finish).toBeTypeOf('function'))
    const pending = queryClient.getMutationCache().getAll().filter(mutation => mutation.state.status === 'pending')
    expect(pending).toHaveLength(1)
    expect(pending[0]?.state.variables).toBeUndefined()
    expect(JSON.stringify(pending[0]?.state)).not.toContain('Özel Deniz')
    await act(async () => finish(json({ id: NEW_ID }, 201)))
    await waitFor(() => expect(screen.getByLabelText('Test location')).toHaveTextContent(NEW_ID))
    expect(posts).toEqual([{ path: '/api/v1/prompt-requests', payload: {
      raw_prompt: SOURCE.raw_prompt, user_context: composed + '\n  İsteğe bağlı ek.', output_language: 'de-DE',
      target_mode: 'openai', task_profile: 'code_review', prompt_detail_level: 'high',
      response_detail_preference: 'minimal', reasoning_preference: 'low',
    } }])
    expect(screen.getByLabelText('Test location').textContent)
      .toBe(JSON.stringify([`/prompt-requests/${NEW_ID}`, '', { justCreated: true }]))
    expect(queryClient.getMutationCache().getAll().every(mutation =>
      mutation.state.variables === undefined && (mutation.state.data === undefined || mutation.state.data === NEW_ID))).toBe(true)
  })

  it('recomposes ordered answers from the original context and does not truncate an oversized draft', async () => {
    const { posts } = view({ questions: QUESTIONS, source: { ...SOURCE, output_language: null, user_context: 'c'.repeat(19_900) } })
    await prepare(['ilk', 'ikinci', 'son'])
    const draft = screen.getByLabelText(/Proje Bağlamı/)
    const text = (draft as HTMLTextAreaElement).value
    expect(text).toBe('c'.repeat(19_900) + '\n\nEk Açıklama Yanıtları\n\n' +
      QUESTIONS.map((value, i) => `Soru ${i + 1}: ${value}\nYanıt ${i + 1}: ${['ilk', 'ikinci', 'son'][i]}`).join('\n\n'))
    expect(screen.getByText(new RegExp(text.length.toLocaleString('tr-TR').replace('.', '\\.')))).toBeInTheDocument()
    expect(screen.getByLabelText(/Çıktı Dili/)).toHaveValue('')
    expect(screen.getByRole('button', { name: SAVE })).toBeDisabled()
    fireEvent.submit(screen.getByRole('form', { name: 'Prompt gönderim formu' }))
    expect(posts).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Yanıtlara Dön' }))
    expect(screen.getByRole('textbox', { name: 'Yanıt 3' })).toHaveValue('son')
    await prepare(['bir', 'iki', 'üç'])
    expect((screen.getByLabelText(/Proje Bağlamı/) as HTMLTextAreaElement).value.match(/Ek Açıklama Yanıtları/g)).toHaveLength(1)
    fireEvent.change(screen.getByLabelText(/Proje Bağlamı/), { target: { value: 'Kısaltılmış bağlam' } })
    expect(screen.getByRole('button', { name: SAVE })).toBeEnabled()
    fireEvent.change(screen.getByLabelText(/Proje Bağlamı/), { target: { value: '🌊'.repeat(20_000) } })
    expect(screen.getByRole('button', { name: SAVE })).toBeEnabled()
    fireEvent.change(screen.getByLabelText(/Proje Bağlamı/), { target: { value: '🌊'.repeat(20_001) } })
    expect(screen.getByRole('button', { name: SAVE })).toBeDisabled()
  })

  it.each(['PENDING', 'GENERATING', 'EVALUATING', 'REVISING', 'COMPLETED', 'FAILED'] as const)('offers no answer form for %s', async status => {
    view({ status })
    await screen.findByText(/Son okunan kayıtlı durum:/)
    await screen.findByText(status === 'COMPLETED' ? /Yürütme tamamlandı; sonuç bütünlüğü doğrulandı/ :
      status === 'FAILED' ? /Yürütme başarısız; başarılı bir optimizasyon sonucu sunulmuyor/ : /Nihai sonuç henüz hazır değil/)
    expect(screen.queryByRole('button', { name: PREPARE })).toBeNull()
  })

  it.each([
    { questions: [] }, { questions: ['1', '2', '3', '4'] }, { questions: ['  '] },
    { questions: ['x'.repeat(1001)] }, { questions: [null] }, { count: 2 },
    { missing: true }, { source: { ...SOURCE, id: OTHER_RUN } },
    { source: { ...SOURCE, task_profile: 42 } }, { badParent: true },
  ])('keeps invalid or unbound clarification read-only (%#)', async options => {
    const { posts } = view(options)
    await screen.findByText(options.badParent || options.questions?.[0] === null
      ? /Sonuç okunamadı/ : /Ek açıklama gerekiyor; nihai prompt sunulmuyor/)
    if (options.missing) expect(screen.getByText(/Hangi çıktı\?/)).toBeVisible()
    expect(screen.queryByRole('button', { name: PREPARE })).toBeNull()
    expect(posts).toEqual([])
  })

  it('pauses Save during a result refresh while preserving edits, and withdraws the draft on revocation', async () => {
    const { queryClient, posts, responses, fetcher } = view()
    await prepare()
    fireEvent.change(screen.getByLabelText(/Proje Bağlamı/), { target: { value: PRIVATE } })
    let finish!: (response: Response) => void
    fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' }))
    await waitFor(() => expect(screen.getByRole('button', { name: SAVE })).toBeDisabled())
    fireEvent.submit(screen.getByRole('form', { name: 'Prompt gönderim formu' }))
    expect(posts).toEqual([])
    finish(json(responses(RUN).terminal))
    await waitFor(() => expect(screen.getByRole('button', { name: SAVE })).toBeEnabled())
    expect(screen.getByLabelText(/Proje Bağlamı/)).toHaveValue(PRIVATE)
    act(() => revokeResultIntegrity(queryClient, RUN, PARENT))
    expect(screen.queryByRole('button', { name: SAVE })).toBeNull()
    expect(posts).toEqual([])
  })

  it('withdraws the review if a refresh replaces questions at the same terminal revision', async () => {
    const { fetcher, responses, posts } = view()
    await prepare()
    fetcher.mockResolvedValueOnce(json({ ...responses(RUN).terminal, clarification_questions: ['Replacement question'] }))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' }))
    await screen.findByText('Replacement question')
    expect(screen.queryByRole('button', { name: SAVE })).toBeNull()
    expect(screen.queryByRole('button', { name: PREPARE })).toBeNull()
    expect(posts).toEqual([])
  })

  it('retains a newer detailed-history revision after cache eviction and reconciles with current reads', async () => {
    const { fetcher, responses, posts, queryClient } = view()
    await prepare()
    const old = responses(RUN)
    const workflow = { ...old.row, state_revision: 6 }
    const summary = { ...old.summary, workflow }
    fetcher.mockResolvedValueOnce(json({ ...old.details, metadata: summary }))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: SAVE })).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş İçeriğini Kapat' }))
    await waitFor(() => expect(queryClient.getQueryState(workflowKeys.content(RUN, PARENT))).toBeUndefined())
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Kapat' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Görüntüle' }))
    await screen.findByText(/Ek açıklama gerekiyor; nihai prompt/)
    expect(screen.queryByRole('button', { name: PREPARE })).toBeNull()
    expect(posts).toEqual([])
    fetcher.mockImplementation(async (url: string) => json(url.includes('history') ? summary :
      url.includes('result') ? { ...old.terminal, workflow } : old.row))
    fireEvent.click(screen.getByRole('button', { name: 'Açıklama Kayıtlarını Yenile' }))
    expect(await screen.findByRole('button', { name: PREPARE })).toBeEnabled()
  })

  it('rejects a malformed higher history revision without moving the floor', async () => {
    const { fetcher, responses, queryClient } = view()
    await prepare()
    const old = responses(RUN)
    fetcher.mockResolvedValueOnce(json({ ...old.details, clarification_questions: [],
      metadata: { ...old.summary, workflow: { ...old.row, state_revision: 900 } } }))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }))
    await waitFor(() => expect(readResultIntegrity(queryClient, RUN, PARENT).clarificationBlocked).toBe(true))
    expect(readResultIntegrity(queryClient, RUN, PARENT).observedRevision).toBe(5)
    expect(screen.queryByRole('button', { name: SAVE })).toBeNull()
  })

  it('keeps original CRLF in context and questions when appending text to the review', async () => {
    const context = '  İlk\r\nİkinci\n  Son\r\n'
    const question = 'Hangi çıktı?\r\nHangi biçim?'
    const { posts } = view({ questions: [question], source: { ...SOURCE, user_context: context } })
    await prepare()
    const input = screen.getByLabelText<HTMLTextAreaElement>(/Proje Bağlamı/)
    const normalized = input.value
    input.setSelectionRange(normalized.length, normalized.length)
    fireEvent(input, new InputEvent('beforeinput', { bubbles: true, inputType: 'insertText', data: ' Ek.' }))
    fireEvent.change(input, { target: { value: normalized + ' Ek.' } })
    fireEvent.click(screen.getByRole('button', { name: SAVE }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]!.payload.user_context).toBe(`${context}\n\nEk Açıklama Yanıtları\n\nSoru 1: ${question}\nYanıt 1: ${PRIVATE} Ek.`)
  })

  it.each(['source', 'questions', 'foreign parent'] as const)('retains a known %s contradiction after history is closed and the result is refreshed', async kind => {
    const { fetcher, responses, posts } = view()
    await prepare()
    const details = responses(RUN).details
    fetcher.mockResolvedValueOnce(json(kind === 'source'
      ? { ...details, source: { ...SOURCE, raw_prompt: 'Different source text' } }
      : kind === 'questions' ? { ...details, clarification_questions: ['Different terminal question'] }
        : { ...details, source: { ...SOURCE, id: OTHER_RUN } }))
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Sürümlerini ve Değerlendirmeleri Yükle' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: SAVE })).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Geçmiş İçeriğini Kapat' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' }))
    await screen.findByText(/Hangi çıktı\?/)
    expect(screen.queryByRole('button', { name: PREPARE })).toBeNull()
    expect(posts).toEqual([])
  })

  it('clears the draft on workflow switch and ignores a late old response', async () => {
    const { fetcher, responses, posts } = view()
    await prepare()
    fireEvent.click(screen.getByRole('button', { name: 'Kayıtlı Workflow’ları Görüntüle' }))
    const picker = await screen.findByLabelText('Bu promptun workflow’ları')
    await within(picker).findByRole('option', { name: new RegExp(OTHER_RUN.slice(0, 8)) })
    let finish!: (response: Response) => void
    fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    fireEvent.click(screen.getByRole('button', { name: 'Sonucu Yenile' }))
    await waitFor(() => expect(finish).toBeTypeOf('function'))
    fireEvent.change(picker, { target: { value: OTHER_RUN } })
    expect(await screen.findByRole('textbox', { name: 'Yanıt 1' })).toHaveValue('')
    await act(async () => finish(json(responses(RUN).terminal)))
    expect(screen.getByRole('textbox', { name: 'Yanıt 1' })).toHaveValue('')
    expect(screen.queryByRole('button', { name: SAVE })).toBeNull()
    expect(posts).toEqual([])
  })

  it('does not retry an ambiguous POST failure or expose its diagnostic text', async () => {
    const { fetcher } = view()
    await prepare()
    fetcher.mockRejectedValueOnce(new Error(PRIVATE))
    fireEvent.click(screen.getByRole('button', { name: SAVE }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Prompt Geçmişini kontrol edin/)
    expect(screen.getByRole('alert')).not.toHaveTextContent(PRIVATE)
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1)
    expect(screen.getByLabelText('Test location')).toHaveTextContent(PARENT)
  })

  it('allows a dispatched POST to finish without navigating after the result view closes', async () => {
    const { fetcher } = view()
    await prepare()
    let finish!: (response: Response) => void
    fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    fireEvent.click(screen.getByRole('button', { name: SAVE }))
    await waitFor(() => expect(finish).toBeTypeOf('function'))
    fireEvent.click(screen.getByRole('button', { name: 'Sonuç ve Geçmişi Kapat' }))
    await act(async () => finish(json({ id: NEW_ID }, 201)))
    expect(screen.getByLabelText('Test location')).toHaveTextContent(PARENT)
    expect(screen.getByLabelText('Test location')).not.toHaveTextContent(NEW_ID)
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1)
  })

  it.each([true, false])('ignores a late save outcome on a different parent (%s success)', async success => {
    const { fetcher } = view()
    await prepare()
    let finish!: () => void
    fetcher.mockImplementationOnce(() => new Promise((resolve, reject) => {
      finish = () => success ? resolve(json({ id: NEW_ID }, 201)) : reject(new Error(PRIVATE))
    }))
    fireEvent.click(screen.getByRole('button', { name: SAVE }))
    await waitFor(() => expect(finish).toBeTypeOf('function'))
    fireEvent.click(screen.getByRole('button', { name: 'Open another parent' }))
    await waitFor(() => expect(screen.getByLabelText('Test location')).toHaveTextContent(OTHER_RUN))
    await act(async () => finish())
    expect(screen.getByLabelText('Test location')).toHaveTextContent(OTHER_RUN)
    expect(screen.getByLabelText('Test location')).not.toHaveTextContent(NEW_ID)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1)
  })

  it('does not treat the original ID in a POST response as a new saved request', async () => {
    const { fetcher } = view()
    await prepare()
    fetcher.mockResolvedValueOnce(json({ id: PARENT }, 201))
    fireEvent.click(screen.getByRole('button', { name: SAVE }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Prompt Geçmişini kontrol edin/)
    expect(screen.getByLabelText('Test location')).toHaveTextContent(`workflow=${RUN}&view=result`)
  })

  it('keeps echoed server metadata out of follow-up mutation errors', async () => {
    const { fetcher, queryClient } = view()
    await prepare()
    fetcher.mockResolvedValueOnce(Response.json({
      type: 'about:blank', title: 'Unprocessable Entity', status: 422, detail: PRIVATE,
      instance: '/api/v1/prompt-requests', request_id: PRIVATE, error_code: PRIVATE,
      errors: [{ field: PRIVATE, message: PRIVATE, code: PRIVATE },
        { field: 'user_context', message: PRIVATE, code: 'string_too_long' }],
    }, { status: 422, headers: { 'Content-Type': 'application/problem+json' } }))
    fireEvent.click(screen.getByRole('button', { name: SAVE }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/azami karakter sınırını aşıyor/)
    expect(alert).not.toHaveTextContent('Özel Deniz')
    expect(JSON.stringify(queryClient.getMutationCache().getAll().map(mutation => mutation.state))).not.toContain('Özel Deniz')
  })
})
