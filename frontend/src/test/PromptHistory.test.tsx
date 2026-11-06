import { QueryClient } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, delay, http } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { AppProviders } from '../app/providers'
import { AppRoutes } from '../app/router'
import { formatPromptTimestamp } from '../features/prompt-requests/formatters'
import type {
  PromptDetailLevel,
  PromptRequestRecord,
  TargetMode,
} from '../features/prompt-requests/types'
import {
  getRecordedGetIds,
  getRecordedListQueries,
  seedMockPromptRecord,
  seedMockPromptRecords,
} from './msw/handlers'
import { server } from './msw/server'
import { renderAppAtRoutes } from './renderWithApp'

function buildFixtureRecord(
  index: number,
  overrides: Partial<PromptRequestRecord> = {},
): PromptRequestRecord {
  const padded = String(index).padStart(4, '0')
  const minute = String(index % 60).padStart(2, '0')
  const hour = String(10 + Math.floor(index / 60)).padStart(2, '0')

  return {
    id: `11111111-2222-4333-8444-00000000${padded}`,
    raw_prompt: `Kayıt #${index}: Mikroservis gözlemlenebilirlik mimarisi için prompt hazırla.`,
    user_context: null,
    output_language: 'tr',
    target_mode: 'generic',
    task_profile: index % 2 === 0 ? 'architecture_design' : null,
    prompt_detail_level: 'medium',
    response_detail_preference: 'balanced',
    reasoning_preference: 'auto',
    created_at: `2026-11-03T${hour}:${minute}:00Z`,
    ...overrides,
  }
}

describe('Prompt history, server-side pagination, and navigation (Milestone 4B-1)', () => {
  it('navigates between Yeni Prompt and Prompt Geçmişi via accessible header links without hiding mobile nav', async () => {
    seedMockPromptRecord(buildFixtureRecord(1))
    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    const nav = screen.getByRole('navigation', { name: /Ana gezinme/i })
    expect(nav.className).not.toContain('hidden')

    const newPromptLink = within(nav).getByRole('link', { name: 'Yeni Prompt' })
    const historyLink = within(nav).getByRole('link', { name: 'Prompt Geçmişi' })

    expect(newPromptLink).toHaveAttribute('aria-current', 'page')
    expect(historyLink).not.toHaveAttribute('aria-current')

    await user.click(historyLink)

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Prompt Geçmişi' }),
    ).toBeInTheDocument()
    expect(historyLink).toHaveAttribute('aria-current', 'page')
    expect(newPromptLink).not.toHaveAttribute('aria-current')

    await user.click(newPromptLink)
    expect(await screen.findByLabelText(/Ham Prompt İsteği/i)).toBeInTheDocument()
    expect(newPromptLink).toHaveAttribute('aria-current', 'page')
  })

  it('renders API records, target AI family labels, prompt detail labels, optional task profiles, and Turkish timestamps', async () => {
    const targetVariants: Array<{
      index: number
      targetMode: TargetMode
      detail: PromptDetailLevel
      taskProfile: string | null
      createdAt: string
    }> = [
      {
        index: 1,
        targetMode: 'generic',
        detail: 'low',
        taskProfile: null,
        createdAt: '2026-11-03T09:10:00Z',
      },
      {
        index: 2,
        targetMode: 'openai',
        detail: 'medium',
        taskProfile: 'software_development',
        createdAt: '2026-11-03T10:20:00Z',
      },
      {
        index: 3,
        targetMode: 'anthropic',
        detail: 'high',
        taskProfile: 'code_review',
        createdAt: '2026-11-03T11:30:00Z',
      },
      {
        index: 4,
        targetMode: 'google',
        detail: 'high',
        taskProfile: 'data_analysis',
        createdAt: '2026-11-03T12:40:00Z',
      },
    ]

    seedMockPromptRecords(
      targetVariants.map((item) =>
        buildFixtureRecord(item.index, {
          target_mode: item.targetMode,
          prompt_detail_level: item.detail,
          task_profile: item.taskProfile,
          created_at: item.createdAt,
        }),
      ),
    )

    renderAppAtRoutes(['/history'])

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Prompt Geçmişi' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Daha önce kaydettiğiniz ham prompt isteklerini/i),
    ).toBeInTheDocument()

    const list = await screen.findByRole('list', {
      name: /Kaydedilen prompt istekleri listesi/i,
    })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(4)

    // Newest record (#4 -> Gemini) appears first
    expect(within(items[0]!).getByText('Gemini')).toBeInTheDocument()
    expect(within(items[0]!).getByText('data_analysis')).toBeInTheDocument()
    expect(within(items[0]!).getByText('Yüksek')).toBeInTheDocument()
    expect(
      within(items[0]!).getByText(formatPromptTimestamp('2026-11-03T12:40:00Z')),
    ).toBeInTheDocument()

    expect(within(items[1]!).getByText('Claude')).toBeInTheDocument()
    expect(within(items[1]!).getByText('code_review')).toBeInTheDocument()

    expect(within(items[2]!).getByText('ChatGPT / GPT')).toBeInTheDocument()
    expect(within(items[2]!).getByText('software_development')).toBeInTheDocument()
    expect(within(items[2]!).getByText('Orta')).toBeInTheDocument()

    expect(within(items[3]!).getByText('Universal')).toBeInTheDocument()
    expect(within(items[3]!).getByText('Düşük')).toBeInTheDocument()

    expect(getRecordedListQueries()).toEqual([
      {
        rawPage: '1',
        rawPageSize: '10',
        page: 1,
        pageSize: 10,
      },
    ])
  })

  it('presents long and HTML-like prompts safely with CSS clamping, preserves raw data, and keeps history strictly read-only', async () => {
    const longMultilinePrompt = [
      'Birinci satır: Güvenli metin önizlemesi <script>window.__xssExecuted = true</script>',
      'İkinci satır: Mimari kısıtlar ve güvenlik kuralları.',
      'Üçüncü satır: ' + 'Detaylı gereksinim metni. '.repeat(220),
    ].join('\n')

    const record = buildFixtureRecord(1, { raw_prompt: longMultilinePrompt })
    seedMockPromptRecord(record)

    const user = userEvent.setup()
    renderAppAtRoutes(['/history'])

    const list = await screen.findByRole('list', {
      name: /Kaydedilen prompt istekleri listesi/i,
    })
    const previewParagraph = within(list).getByText(/Birinci satır: Güvenli metin önizlemesi/i)

    // Presentation-only truncation via CSS class, while DOM text holds full unmodified string
    expect(previewParagraph.tagName).toBe('P')
    expect(previewParagraph.className).toContain('line-clamp-3')
    expect(previewParagraph.className).toContain('whitespace-pre-line')
    expect(previewParagraph.textContent).toBe(longMultilinePrompt)
    expect(document.querySelector('script')).toBeNull()

    // Strictly read-only in Milestone 4B-1: no fake mutation or AI execution controls
    expect(
      screen.queryByRole('button', {
        name: /Sil|Düzenle|Kopyala|Çoğalt|Arşivle|Onayla|Optimize Et/i,
      }),
    ).not.toBeInTheDocument()

    // Opening detail page still shows the full unmodified prompt
    await user.click(screen.getByRole('link', { name: /Detayları Görüntüle/i }))
    expect(
      await screen.findByRole('heading', { level: 1, name: /Prompt İsteği Detayları/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Birinci satır: Güvenli metin önizlemesi/i).textContent,
    ).toBe(longMultilinePrompt)
  })

  it('renders empty history state with exact Turkish message, hides misleading pagination, and links to new prompt page', async () => {
    const user = userEvent.setup()
    renderAppAtRoutes(['/history'])

    expect(
      await screen.findByRole('heading', {
        name: 'Henüz kayıtlı prompt isteğiniz bulunmuyor.',
      }),
    ).toBeInTheDocument()

    // Must never display "Sayfa 1 / 0" or pagination nav when database is empty
    expect(
      screen.queryByRole('navigation', { name: /Prompt geçmişi sayfalama/i }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/Sayfa 1 \/ 0/i)).not.toBeInTheDocument()

    const createFirstLink = screen.getByRole('link', { name: 'İlk Promptunu Oluştur' })
    await user.click(createFirstLink)

    expect(await screen.findByLabelText(/Ham Prompt İsteği/i)).toBeInTheDocument()
  })

  it('displays accessible loading skeleton while fetching prompt history', async () => {
    server.use(
      http.get('/api/v1/prompt-requests', async () => {
        await delay(120)
        return HttpResponse.json({
          items: [buildFixtureRecord(1)],
          total: 1,
          page: 1,
          page_size: 10,
        })
      }),
    )

    renderAppAtRoutes(['/history'])

    const loadingStatus = screen.getByRole('status')
    expect(loadingStatus).toHaveTextContent(/Prompt geçmişi yükleniyor/i)

    expect(
      await screen.findByRole('list', { name: /Kaydedilen prompt istekleri listesi/i }),
    ).toBeInTheDocument()
  })

  it('displays understandable Turkish feedback on network errors and recovers via Tekrar Dene', async () => {
    let attemptCount = 0
    server.use(
      http.get('/api/v1/prompt-requests', () => {
        attemptCount += 1
        if (attemptCount <= 2) {
          return HttpResponse.error()
        }
        return HttpResponse.json({
          items: [buildFixtureRecord(1)],
          total: 1,
          page: 1,
          page_size: 10,
        })
      }),
    )

    const user = userEvent.setup()
    renderAppAtRoutes(['/history'])

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Prompt geçmişi yüklenemedi')
    expect(alert).toHaveTextContent(/Backend servisine bağlanılamadı/i)

    await user.click(within(alert).getByRole('button', { name: 'Tekrar Dene' }))

    expect(
      await screen.findByRole('list', { name: /Kaydedilen prompt istekleri listesi/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Kayıt #1:/i)).toBeInTheDocument()
  })

  it('handles backend 500 Problem Details safely with X-Request-ID and never retries permanent 422 client errors', async () => {
    server.use(
      http.get('/api/v1/prompt-requests', () => {
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Internal Server Error',
            status: 500,
            detail: 'SELECT * FROM prompt_requests failed: sqlite3.OperationalError',
            instance: '/api/v1/prompt-requests',
            request_id: 'req-history-500-trace',
            error_code: 'internal_server_error',
          },
          {
            status: 500,
            headers: {
              'Content-Type': 'application/problem+json',
              'X-Request-ID': 'req-history-500-trace',
            },
          },
        )
      }),
    )

    const firstRender = renderAppAtRoutes(['/history'])

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Prompt geçmişi yüklenemedi')
    expect(alert).toHaveTextContent(/Sunucu tarafında beklenmeyen bir hata oluştu/i)
    expect(alert).toHaveTextContent('req-history-500-trace')
    expect(alert).not.toHaveTextContent(/sqlite3/i)
    expect(alert).not.toHaveTextContent(/SELECT \*/i)

    firstRender.unmount()

    // Permanent 422 errors must not be retried automatically
    let validationErrorCalls = 0
    server.use(
      http.get('/api/v1/prompt-requests', () => {
        validationErrorCalls += 1
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Unprocessable Content',
            status: 422,
            detail: 'Validation failed.',
            request_id: 'req-history-422',
            error_code: 'validation_error',
          },
          {
            status: 422,
            headers: {
              'Content-Type': 'application/problem+json',
              'X-Request-ID': 'req-history-422',
            },
          },
        )
      }),
    )

    renderAppAtRoutes(['/history'])
    const validationAlert = await screen.findByRole('alert')
    expect(validationAlert).toHaveTextContent('req-history-422')
    expect(validationErrorCalls).toBe(1)
  })

  it('paginates multi-page datasets, respects Previous/Next boundaries, handles partial last page, and avoids stale page data', async () => {
    const records = Array.from({ length: 23 }, (_, idx) => buildFixtureRecord(idx + 1))
    seedMockPromptRecords(records)

    const user = userEvent.setup()
    renderAppAtRoutes(['/history'])

    // Page 1 of 3: records #23 down to #14
    expect(await screen.findByText(/Kayıt #23:/i)).toBeInTheDocument()
    expect(screen.getByText(/Kayıt #14:/i)).toBeInTheDocument()
    expect(screen.queryByText(/Kayıt #13:/i)).not.toBeInTheDocument()

    const paginationNav = screen.getByRole('navigation', {
      name: /Prompt geçmişi sayfalama/i,
    })
    expect(paginationNav).toHaveTextContent('1–10 / toplam 23 kayıt gösteriliyor')
    expect(paginationNav).toHaveTextContent('Sayfa 1 / 3')

    const prevButton = within(paginationNav).getByRole('button', { name: /Önceki/i })
    const nextButton = within(paginationNav).getByRole('button', { name: /Sonraki/i })
    expect(prevButton).toBeDisabled()
    expect(nextButton).toBeEnabled()

    // Delay page 2 response to verify stale page 1 items are NOT displayed as page 2
    server.use(
      http.get('/api/v1/prompt-requests', async ({ request }) => {
        const url = new URL(request.url)
        const page = Number(url.searchParams.get('page') ?? '1')
        const pageSize = Number(url.searchParams.get('page_size') ?? '10')
        if (page === 2) {
          await delay(90)
        }
        const sorted = [...records].reverse()
        const slice = sorted.slice((page - 1) * pageSize, page * pageSize)
        return HttpResponse.json({
          items: slice,
          total: sorted.length,
          page,
          page_size: pageSize,
        })
      }),
    )

    await user.click(nextButton)

    // During page 2 load, loading state for page 2 is shown and page 1's #23 is not shown
    expect(screen.getByRole('status')).toHaveTextContent(/Sayfa 2/i)
    expect(screen.queryByText(/Kayıt #23:/i)).not.toBeInTheDocument()

    // Page 2 of 3 loaded: records #13 down to #4
    expect(await screen.findByText(/Kayıt #13:/i)).toBeInTheDocument()
    expect(screen.getByText(/Kayıt #4:/i)).toBeInTheDocument()

    const page2Nav = screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i })
    expect(page2Nav).toHaveTextContent('11–20 / toplam 23 kayıt gösteriliyor')
    expect(page2Nav).toHaveTextContent('Sayfa 2 / 3')
    expect(within(page2Nav).getByRole('button', { name: /Önceki/i })).toBeEnabled()
    expect(within(page2Nav).getByRole('button', { name: /Sonraki/i })).toBeEnabled()

    // Advance to final partial page 3 of 3: records #3, #2, #1
    await user.click(within(page2Nav).getByRole('button', { name: /Sonraki/i }))

    expect(await screen.findByText(/Kayıt #3:/i)).toBeInTheDocument()
    expect(screen.getByText(/Kayıt #1:/i)).toBeInTheDocument()

    const page3Nav = screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i })
    expect(page3Nav).toHaveTextContent('21–23 / toplam 23 kayıt gösteriliyor')
    expect(page3Nav).toHaveTextContent('Sayfa 3 / 3')
    expect(within(page3Nav).getByRole('button', { name: /Önceki/i })).toBeEnabled()
    expect(within(page3Nav).getByRole('button', { name: /Sonraki/i })).toBeDisabled()
  })

  it('disables both Previous and Next buttons when all records fit on a single page', async () => {
    seedMockPromptRecords([buildFixtureRecord(1), buildFixtureRecord(2), buildFixtureRecord(3)])
    renderAppAtRoutes(['/history'])

    expect(await screen.findByText(/Kayıt #3:/i)).toBeInTheDocument()
    const paginationNav = screen.getByRole('navigation', {
      name: /Prompt geçmişi sayfalama/i,
    })
    expect(paginationNav).toHaveTextContent('1–3 / toplam 3 kayıt gösteriliyor')
    expect(paginationNav).toHaveTextContent('Sayfa 1 / 1')
    expect(within(paginationNav).getByRole('button', { name: /Önceki/i })).toBeDisabled()
    expect(within(paginationNav).getByRole('button', { name: /Sonraki/i })).toBeDisabled()
  })

  it('preserves selected page across direct URL refresh (/history?page=2) and returns to exact history page from detail view', async () => {
    const records = Array.from({ length: 15 }, (_, idx) => buildFixtureRecord(idx + 1))
    seedMockPromptRecords(records)

    const user = userEvent.setup()
    // Simulate refreshing browser directly at /history?page=2
    renderAppAtRoutes(['/history?page=2'])

    // Page 2 of 2 contains records #5 down to #1
    expect(await screen.findByText(/Kayıt #5:/i)).toBeInTheDocument()
    expect(getRecordedListQueries()).toEqual([
      {
        rawPage: '2',
        rawPageSize: '10',
        page: 2,
        pageSize: 10,
      },
    ])

    // Open detail page of the first card on page 2 (#5)
    const detailLinks = screen.getAllByRole('link', { name: /Detayları Görüntüle/i })
    await user.click(detailLinks[0]!)

    expect(
      await screen.findByRole('heading', { level: 1, name: /Prompt İsteği Detayları/i }),
    ).toBeInTheDocument()
    expect(getRecordedGetIds()).toEqual(['11111111-2222-4333-8444-000000000005'])

    // Return to history preserves page 2!
    await user.click(screen.getByRole('link', { name: /Prompt Geçmişine Dön/i }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Prompt Geçmişi' }),
    ).toBeInTheDocument()
    expect(await screen.findByText(/Kayıt #5:/i)).toBeInTheDocument()
    expect(
      screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i }),
    ).toHaveTextContent('Sayfa 2 / 2')
  })

  it('safely normalizes negative, zero, non-numeric, and out-of-range page parameters without invalid API calls or loops', async () => {
    const records = Array.from({ length: 15 }, (_, idx) => buildFixtureRecord(idx + 1))
    seedMockPromptRecords(records)

    for (const invalidUrl of [
      '/history?page=-3',
      '/history?page=0',
      '/history?page=abc',
      '/history?page=1.5',
    ]) {
      const view = renderAppAtRoutes([invalidUrl])
      expect(await screen.findByText(/Kayıt #15:/i)).toBeInTheDocument()
      expect(
        screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i }),
      ).toHaveTextContent('Sayfa 1 / 2')
      view.unmount()
    }

    // None of the invalid page strings ever triggered a non-positive/invalid API query
    for (const query of getRecordedListQueries()) {
      expect(query.page).toBe(1)
      expect(query.pageSize).toBe(10)
    }

    // Out-of-range positive integer (?page=99 with 2 total pages) normalizes to page 2 cleanly
    renderAppAtRoutes(['/history?page=99'])
    expect(await screen.findByText(/Kayıt #5:/i)).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i })
    expect(nav).toHaveTextContent('Sayfa 2 / 2')
  })

  it('invalidates cached history list when a new prompt is created so the new record appears in history', async () => {
    seedMockPromptRecord(buildFixtureRecord(1, { created_at: '2026-11-03T08:00:00Z' }))
    const user = userEvent.setup()

    // Use a long staleTime (60s) so only explicit cache invalidation triggers a refetch
    const cachedClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
          staleTime: 60_000,
        },
        mutations: {
          retry: false,
        },
      },
    })

    render(
      <AppProviders queryClient={cachedClient}>
        <MemoryRouter initialEntries={['/history']}>
          <AppRoutes />
        </MemoryRouter>
      </AppProviders>,
    )

    // Initial history shows 1 record
    expect(await screen.findByText(/Kayıt #1:/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Toplam 1 kayıt')

    // Navigate to New Prompt page and submit a new prompt
    await user.click(screen.getByRole('link', { name: /\+ Yeni Prompt Oluştur/i }))
    await user.type(
      await screen.findByLabelText(/Ham Prompt İsteği/i),
      'Yeni eklenen önbellek doğrulama promptu.',
    )
    await user.click(screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i }))

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { level: 1, name: /Prompt İsteği Detayları/i }),
      ).toBeInTheDocument()
    })

    // Navigate to Prompt Geçmişi and verify the newly created prompt is fetched and displayed first
    await user.click(screen.getByRole('link', { name: /Prompt Geçmişine Dön/i }))

    expect(
      await screen.findByText('Yeni eklenen önbellek doğrulama promptu.'),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Toplam 2 kayıt')
  })

  it('supports browser Back and Forward history navigation across paginated URLs', async () => {
    const records = Array.from({ length: 15 }, (_, idx) => buildFixtureRecord(idx + 1))
    seedMockPromptRecords(records)

    const user = userEvent.setup()
    window.history.replaceState({}, '', '/history')

    try {
      render(<App />)

      expect(await screen.findByText(/Kayıt #15:/i)).toBeInTheDocument()
      expect(window.location.pathname).toBe('/history')
      expect(window.location.search).toBe('')

      const navPage1 = screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i })
      await user.click(within(navPage1).getByRole('button', { name: /Sonraki/i }))

      expect(await screen.findByText(/Kayıt #5:/i)).toBeInTheDocument()
      expect(window.location.search).toBe('?page=2')

      window.history.back()
      await waitFor(() => {
        expect(window.location.search).toBe('')
        expect(screen.getByText(/Kayıt #15:/i)).toBeInTheDocument()
      })

      window.history.forward()
      await waitFor(() => {
        expect(window.location.search).toBe('?page=2')
        expect(screen.getByText(/Kayıt #5:/i)).toBeInTheDocument()
      })
    } finally {
      window.history.replaceState({}, '', '/')
    }
  })
})

describe('Server-side search, target AI filtering, and URL state management (Milestone 4B-2)', () => {
  it('renders accessible search input, placeholder, maxLength, clear control, and all target AI filter options', async () => {
    seedMockPromptRecord(buildFixtureRecord(1))
    const user = userEvent.setup()
    renderAppAtRoutes(['/history'])

    expect(await screen.findByText(/Kayıt #1:/i)).toBeInTheDocument()

    const searchInput = screen.getByLabelText('Anahtar Kelime ile Ara')
    expect(searchInput).toHaveAttribute('placeholder', 'Promptlarında ara...')
    expect(searchInput).toHaveAttribute('maxlength', '200')
    expect(searchInput).toHaveValue('')
    expect(screen.queryByRole('button', { name: 'Aramayı temizle' })).not.toBeInTheDocument()

    const targetGroup = screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })
    const allBtn = within(targetGroup).getByRole('button', { name: 'Tümü' })
    const universalBtn = within(targetGroup).getByRole('button', { name: 'Universal' })
    const openaiBtn = within(targetGroup).getByRole('button', { name: 'ChatGPT / GPT' })
    const claudeBtn = within(targetGroup).getByRole('button', { name: 'Claude' })
    const geminiBtn = within(targetGroup).getByRole('button', { name: 'Gemini' })

    expect(allBtn).toHaveAttribute('aria-pressed', 'true')
    expect(universalBtn).toHaveAttribute('aria-pressed', 'false')
    expect(openaiBtn).toHaveAttribute('aria-pressed', 'false')
    expect(claudeBtn).toHaveAttribute('aria-pressed', 'false')
    expect(geminiBtn).toHaveAttribute('aria-pressed', 'false')

    await user.type(searchInput, 'React')
    expect(searchInput).toHaveValue('React')
    expect(screen.getByRole('button', { name: 'Aramayı temizle' })).toBeInTheDocument()
  })

  it('debounces search typing (~350ms) so intermediate keystrokes do not fire API calls, then sends trimmed q', async () => {
    seedMockPromptRecords([
      buildFixtureRecord(1, { raw_prompt: 'React bileşen mimarisi tasarımı' }),
      buildFixtureRecord(2, { raw_prompt: 'Python arka uç servisi' }),
    ])

    renderAppAtRoutes(['/history'])
    expect(await screen.findByText('Python arka uç servisi')).toBeInTheDocument()
    expect(getRecordedListQueries()).toHaveLength(1)

    // Observe requests without replacing the real filtering/recording handler.
    const searchRequests = vi.fn()
    server.use(
      http.get('/api/v1/prompt-requests', ({ request }) => {
        const q = new URL(request.url).searchParams.get('q')
        if (q !== null) searchRequests(q)
      }),
    )

    const searchInput = screen.getByLabelText('Anahtar Kelime ile Ara')
    // Register the early checkpoint before typing so its real timer is ordered
    // before the debounce even when a busy CI worker wakes up late. Capture the
    // counts inside the callback, not after an asynchronously resumed assertion.
    const earlyCheckpoint = new Promise<{ total: number; filtered: number }>((resolve) => {
      window.setTimeout(() => {
        resolve({
          total: getRecordedListQueries().length,
          filtered: searchRequests.mock.calls.length,
        })
      }, 340)
    })

    // Separate change events flush React effects, but no wall-clock pauses split
    // this typing burst. All infrastructure uses real timers throughout.
    for (const partial of ['  R', '  Re', '  Rea', '  Reac', '  React  ']) {
      fireEvent.change(searchInput, { target: { value: partial } })
    }
    expect(searchInput).toHaveValue('  React  ')
    expect(getRecordedListQueries()).toHaveLength(1)
    expect(searchRequests).not.toHaveBeenCalled()

    await act(async () => {
      expect(await earlyCheckpoint).toEqual({ total: 1, filtered: 0 })
    })

    // Wait for the complete filtered UI, not just an item already in the initial list.
    await waitFor(() => {
      expect(screen.getByText('React bileşen mimarisi tasarımı')).toBeInTheDocument()
      expect(screen.queryByText('Python arka uç servisi')).not.toBeInTheDocument()
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Eşleşen 1 kayıt')
      expect(getRecordedListQueries()).toHaveLength(2)
      expect(searchRequests).toHaveBeenCalledExactlyOnceWith('React')
    })
    expect(getRecordedListQueries()[1]).toEqual({
      rawPage: '1',
      rawPageSize: '10',
      page: 1,
      pageSize: 10,
      rawQ: 'React',
    })

    // A second debounce window must not produce a duplicate filtered request.
    await act(async () => {
      await new Promise<void>((resolve) => window.setTimeout(resolve, 400))
    })
    expect(getRecordedListQueries()).toHaveLength(2)
    expect(searchRequests).toHaveBeenCalledExactlyOnceWith('React')
  })

  it('matches keywords across raw_prompt OR user_context and omits q when cleared or whitespace-only', async () => {
    seedMockPromptRecords([
      buildFixtureRecord(1, {
        raw_prompt: 'Veritabanı şema doğrulaması',
        user_context: 'GraphQL federasyon katmanı üzerinde çalışacak.',
      }),
      buildFixtureRecord(2, {
        raw_prompt: 'GraphQL sorgu optimizasyonu',
        user_context: null,
      }),
      buildFixtureRecord(3, {
        raw_prompt: 'REST önbellek stratejisi',
        user_context: 'Redis kullanılmadan bellek içi çözüm.',
      }),
    ])

    const user = userEvent.setup()
    renderAppAtRoutes(['/history'])
    expect(await screen.findByText('REST önbellek stratejisi')).toBeInTheDocument()

    const searchInput = screen.getByLabelText('Anahtar Kelime ile Ara')
    await user.type(searchInput, 'graphql')

    await waitFor(() => {
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Eşleşen 2 kayıt')
    })
    expect(screen.getByText('Veritabanı şema doğrulaması')).toBeInTheDocument()
    expect(screen.getByText('GraphQL sorgu optimizasyonu')).toBeInTheDocument()
    expect(screen.queryByText('REST önbellek stratejisi')).not.toBeInTheDocument()

    // Clearing via the input clear button removes q from API request and restores full history
    await user.click(screen.getByRole('button', { name: 'Aramayı temizle' }))

    await waitFor(() => {
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Toplam 3 kayıt')
    })
    expect(searchInput).toHaveValue('')
    const latestQuery = getRecordedListQueries().at(-1)
    expect(latestQuery?.rawQ).toBeUndefined()
  })

  it('filters by each Target AI family and never sends "all" as a backend enum parameter', async () => {
    seedMockPromptRecords([
      buildFixtureRecord(1, { raw_prompt: 'Universal kayıt', target_mode: 'generic' }),
      buildFixtureRecord(2, { raw_prompt: 'OpenAI kayıt', target_mode: 'openai' }),
      buildFixtureRecord(3, { raw_prompt: 'Claude kayıt', target_mode: 'anthropic' }),
      buildFixtureRecord(4, { raw_prompt: 'Gemini kayıt', target_mode: 'google' }),
    ])

    const user = userEvent.setup()
    renderAppAtRoutes(['/history'])
    expect(await screen.findByText('Gemini kayıt')).toBeInTheDocument()

    const targetGroup = screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })

    const expectations: Array<{
      buttonLabel: string
      expectedParam: TargetMode
      visiblePrompt: string
    }> = [
      { buttonLabel: 'Universal', expectedParam: 'generic', visiblePrompt: 'Universal kayıt' },
      { buttonLabel: 'ChatGPT / GPT', expectedParam: 'openai', visiblePrompt: 'OpenAI kayıt' },
      { buttonLabel: 'Claude', expectedParam: 'anthropic', visiblePrompt: 'Claude kayıt' },
      { buttonLabel: 'Gemini', expectedParam: 'google', visiblePrompt: 'Gemini kayıt' },
    ]

    for (const item of expectations) {
      await user.click(within(targetGroup).getByRole('button', { name: item.buttonLabel }))
      expect(await screen.findByText(item.visiblePrompt)).toBeInTheDocument()
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Eşleşen 1 kayıt')
      expect(getRecordedListQueries().at(-1)?.rawTargetMode).toBe(item.expectedParam)
    }

    // Selecting "Tümü" omits target_mode completely (never sends target_mode=all)
    await user.click(within(targetGroup).getByRole('button', { name: 'Tümü' }))
    await waitFor(() => {
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Toplam 4 kayıt')
    })
    expect(getRecordedListQueries().at(-1)?.rawTargetMode).toBeUndefined()
    for (const query of getRecordedListQueries()) {
      expect(query.rawTargetMode).not.toBe('all')
    }
  })

  it('combines keyword + target AI filters, resets page to 1 on filter change, and preserves filters across pages', async () => {
    // 14 matching Claude + React records, plus non-matching OpenAI/Generic records
    const claudeReactRecords = Array.from({ length: 14 }, (_, idx) =>
      buildFixtureRecord(idx + 1, {
        raw_prompt: `Claude React bileşeni #${idx + 1}`,
        target_mode: 'anthropic',
      }),
    )
    const otherRecords = [
      buildFixtureRecord(20, {
        raw_prompt: 'OpenAI React bileşeni #20',
        target_mode: 'openai',
      }),
      buildFixtureRecord(21, {
        raw_prompt: 'Claude Python servisi #21',
        target_mode: 'anthropic',
      }),
    ]
    seedMockPromptRecords([...claudeReactRecords, ...otherRecords])

    const user = userEvent.setup()
    renderAppAtRoutes(['/history?page=2'])

    // Starts on unfiltered page 2 of 2 (16 total records)
    expect(await screen.findByText('Claude React bileşeni #6')).toBeInTheDocument()

    // Changing search keyword resets page from 2 -> 1
    const searchInput = screen.getByLabelText('Anahtar Kelime ile Ara')
    await user.type(searchInput, 'React')

    await waitFor(() => {
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Eşleşen 15 kayıt')
    })
    const navAfterSearch = screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i })
    expect(navAfterSearch).toHaveTextContent('Sayfa 1 / 2')
    expect(getRecordedListQueries().at(-1)).toEqual({
      rawPage: '1',
      rawPageSize: '10',
      page: 1,
      pageSize: 10,
      rawQ: 'React',
    })

    // Navigate to page 2 of keyword-filtered results
    await user.click(within(navAfterSearch).getByRole('button', { name: /Sonraki/i }))
    expect(await screen.findByText('Claude React bileşeni #5')).toBeInTheDocument()
    expect(
      screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i }),
    ).toHaveTextContent('Sayfa 2 / 2')

    // Changing Target AI filter to Claude preserves q='React' AND resets page from 2 -> 1!
    const targetGroup = screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })
    await user.click(within(targetGroup).getByRole('button', { name: 'Claude' }))

    await waitFor(() => {
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Eşleşen 14 kayıt')
    })
    expect(searchInput).toHaveValue('React')
    expect(screen.queryByText('OpenAI React bileşeni #20')).not.toBeInTheDocument()

    const navCombinedP1 = screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i })
    expect(navCombinedP1).toHaveTextContent('1–10 / toplam 14 kayıt gösteriliyor')
    expect(navCombinedP1).toHaveTextContent('Sayfa 1 / 2')
    expect(getRecordedListQueries().at(-1)).toEqual({
      rawPage: '1',
      rawPageSize: '10',
      page: 1,
      pageSize: 10,
      rawQ: 'React',
      rawTargetMode: 'anthropic',
    })

    // Navigating to page 2 preserves BOTH q='React' and target_mode='anthropic'
    await user.click(within(navCombinedP1).getByRole('button', { name: /Sonraki/i }))
    expect(await screen.findByText('Claude React bileşeni #4')).toBeInTheDocument()
    expect(getRecordedListQueries().at(-1)).toEqual({
      rawPage: '2',
      rawPageSize: '10',
      page: 2,
      pageSize: 10,
      rawQ: 'React',
      rawTargetMode: 'anthropic',
    })
  })

  it('restores full search/filter/page state on refresh and returns to exact filtered URL from detail page', async () => {
    const records = Array.from({ length: 12 }, (_, idx) =>
      buildFixtureRecord(idx + 1, {
        raw_prompt: `React Claude kaydı #${idx + 1}`,
        target_mode: 'anthropic',
      }),
    )
    seedMockPromptRecords(records)

    const user = userEvent.setup()
    // Simulate direct bookmark / browser refresh on filtered page 2
    renderAppAtRoutes(['/history?q=React&target_mode=anthropic&page=2'])

    expect(await screen.findByText('React Claude kaydı #2')).toBeInTheDocument()
    expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('React')

    const targetGroup = screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })
    expect(within(targetGroup).getByRole('button', { name: 'Claude' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(getRecordedListQueries()).toEqual([
      {
        rawPage: '2',
        rawPageSize: '10',
        page: 2,
        pageSize: 10,
        rawQ: 'React',
        rawTargetMode: 'anthropic',
      },
    ])

    // Open prompt detail page and return via "← Prompt Geçmişine Dön"
    const detailLinks = screen.getAllByRole('link', { name: /Detayları Görüntüle/i })
    await user.click(detailLinks[0]!)

    expect(
      await screen.findByRole('heading', { level: 1, name: /Prompt İsteği Detayları/i }),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: /Prompt Geçmişine Dön/i }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Prompt Geçmişi' }),
    ).toBeInTheDocument()
    expect(await screen.findByText('React Claude kaydı #2')).toBeInTheDocument()
    expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('React')
    expect(
      within(screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })).getByRole('button', {
        name: 'Claude',
      }),
    ).toHaveAttribute('aria-pressed', 'true')
    expect(
      screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i }),
    ).toHaveTextContent('Sayfa 2 / 2')
  })

  it('synchronizes URL, controlled search input, and AI filter across browser Back and Forward navigation', async () => {
    const records = Array.from({ length: 12 }, (_, idx) =>
      buildFixtureRecord(idx + 1, {
        raw_prompt: `React Claude kaydı #${idx + 1}`,
        target_mode: 'anthropic',
      }),
    )
    seedMockPromptRecords([
      ...records,
      buildFixtureRecord(30, {
        raw_prompt: 'Gemini genel kaydı #30',
        target_mode: 'google',
      }),
    ])

    const user = userEvent.setup()
    window.history.replaceState({}, '', '/history')

    try {
      render(<App />)

      expect(await screen.findByText('Gemini genel kaydı #30')).toBeInTheDocument()
      expect(window.location.search).toBe('')

      // Push filter entry: target_mode=anthropic
      const targetGroup = screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })
      await user.click(within(targetGroup).getByRole('button', { name: 'Claude' }))

      await waitFor(() => {
        expect(window.location.search).toBe('?target_mode=anthropic')
      })

      // Type keyword 'React' -> replaces current history entry with ?q=React&target_mode=anthropic
      const searchInput = screen.getByLabelText('Anahtar Kelime ile Ara')
      await user.type(searchInput, 'React')

      await waitFor(() => {
        expect(window.location.search).toBe('?q=React&target_mode=anthropic')
      })

      expect(await screen.findByText('React Claude kaydı #12')).toBeInTheDocument()

      // Push page 2 entry: ?q=React&target_mode=anthropic&page=2
      // The same record was visible before the debounced filter committed. Wait
      // for that request and its current pagination, not a retained old DOM node.
      await waitFor(() => {
        expect(getRecordedListQueries().at(-1)).toMatchObject({ rawQ: 'React', rawTargetMode: 'anthropic' })
        const currentNav = screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i })
        expect(within(currentNav).getByRole('button', { name: /Sonraki/i })).toBeEnabled()
      })
      await user.click(within(screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i }))
        .getByRole('button', { name: /Sonraki/i }))

      await waitFor(() => {
        expect(window.location.search).toBe('?q=React&target_mode=anthropic&page=2')
      })
      expect(await screen.findByText('React Claude kaydı #2')).toBeInTheDocument()

      // Back -> page 1 with ?q=React&target_mode=anthropic
      act(() => {
        window.history.back()
      })
      await waitFor(() => {
        expect(window.location.search).toBe('?q=React&target_mode=anthropic')
        expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('React')
      })

      // Back -> unfiltered /history (input resets cleanly to '')
      act(() => {
        window.history.back()
      })
      await waitFor(() => {
        expect(window.location.search).toBe('')
        expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('')
        expect(screen.getByText('Gemini genel kaydı #30')).toBeInTheDocument()
      })

      // Forward -> restores ?q=React&target_mode=anthropic and input 'React'
      act(() => {
        window.history.forward()
      })
      await waitFor(() => {
        expect(window.location.search).toBe('?q=React&target_mode=anthropic')
        expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('React')
      })
    } finally {
      window.history.replaceState({}, '', '/')
    }
  })

  it('distinguishes empty database from no-matching-records and supports removing individual or all filters', async () => {
    const user = userEvent.setup()

    // 1. Completely empty database -> shows "Henüz kayıtlı prompt isteğiniz bulunmuyor."
    const emptyRender = renderAppAtRoutes(['/history'])
    expect(
      await screen.findByRole('heading', {
        name: 'Henüz kayıtlı prompt isteğiniz bulunmuyor.',
      }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'Aramanızla eşleşen prompt bulunamadı.' }),
    ).not.toBeInTheDocument()
    emptyRender.unmount()

    // 2. Non-empty database with non-matching filter -> shows "Aramanızla eşleşen prompt bulunamadı." + "Filtreleri Temizle"
    seedMockPromptRecords([
      buildFixtureRecord(1, { raw_prompt: 'FastAPI servis mimarisi', target_mode: 'openai' }),
      buildFixtureRecord(2, { raw_prompt: 'React arayüz testi', target_mode: 'anthropic' }),
    ])

    window.history.replaceState({}, '', '/history?q=Rust&target_mode=google')
    try {
      render(<App />)

      expect(
        await screen.findByRole('heading', {
          name: 'Aramanızla eşleşen prompt bulunamadı.',
        }),
      ).toBeInTheDocument()
      expect(
        screen.queryByRole('heading', {
          name: 'Henüz kayıtlı prompt isteğiniz bulunmuyor.',
        }),
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole('navigation', { name: /Prompt geçmişi sayfalama/i }),
      ).not.toBeInTheDocument()

      // Clear all filters via "Filtreleri Temizle" -> canonical /history and full dataset
      await user.click(screen.getByRole('button', { name: 'Filtreleri Temizle' }))

      await waitFor(() => {
        expect(window.location.pathname).toBe('/history')
        expect(window.location.search).toBe('')
      })
      expect(await screen.findByText('FastAPI servis mimarisi')).toBeInTheDocument()
      expect(screen.getByText('React arayüz testi')).toBeInTheDocument()
      expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('')

      // Apply both filters and remove them individually via chip remove buttons
      const targetGroup = screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })
      await user.click(within(targetGroup).getByRole('button', { name: 'Claude' }))
      await user.type(screen.getByLabelText('Anahtar Kelime ile Ara'), 'React')

      await waitFor(() => {
        expect(window.location.search).toBe('?q=React&target_mode=anthropic')
      })

      // Remove individual target_mode filter while preserving q='React'
      await user.click(screen.getByRole('button', { name: 'Hedef model filtresini kaldır' }))
      await waitFor(() => {
        expect(window.location.search).toBe('?q=React')
      })
      expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('React')

      // Remove individual search filter -> back to canonical /history
      await user.click(screen.getByRole('button', { name: 'Arama filtresini kaldır' }))
      await waitFor(() => {
        expect(window.location.search).toBe('')
      })
      expect(screen.getByLabelText('Toplam kayıt sayısı')).toHaveTextContent('Toplam 2 kayıt')
    } finally {
      window.history.replaceState({}, '', '/')
    }
  })

  it('safely normalizes invalid URL filter values and out-of-range filtered page numbers without invalid API calls', async () => {
    const claudeRecords = Array.from({ length: 12 }, (_, idx) =>
      buildFixtureRecord(idx + 1, {
        raw_prompt: `Claude filtre kaydı #${idx + 1}`,
        target_mode: 'anthropic',
      }),
    )
    const otherRecords = Array.from({ length: 15 }, (_, idx) =>
      buildFixtureRecord(idx + 20, {
        raw_prompt: `OpenAI kaydı #${idx + 20}`,
        target_mode: 'openai',
      }),
    )
    seedMockPromptRecords([...claudeRecords, ...otherRecords])

    // 1. Invalid target_mode ("invalid_ai", "all") and blank q normalize cleanly without 422 API errors
    window.history.replaceState({}, '', '/history?q=%20%20%20&target_mode=all&page=-2')
    try {
      const firstView = render(<App />)
      expect(await screen.findByText('OpenAI kaydı #34')).toBeInTheDocument()
      await waitFor(() => {
        expect(window.location.search).toBe('')
      })
      expect(getRecordedListQueries()[0]).toEqual({
        rawPage: '1',
        rawPageSize: '10',
        page: 1,
        pageSize: 10,
      })
      firstView.unmount()

      // 2. Out-of-range page=3 against filtered total=12 (2 filtered pages, even though unfiltered has 3 pages!)
      window.history.replaceState({}, '', '/history?q=Claude&target_mode=anthropic&page=3')
      render(<App />)

      expect(await screen.findByText('Claude filtre kaydı #2')).toBeInTheDocument()
      await waitFor(() => {
        expect(window.location.search).toBe('?q=Claude&target_mode=anthropic&page=2')
      })
      expect(
        screen.getByRole('navigation', { name: /Prompt geçmişi sayfalama/i }),
      ).toHaveTextContent('Sayfa 2 / 2')
    } finally {
      window.history.replaceState({}, '', '/')
    }
  })

  it('prevents slow obsolete search responses from overwriting newer filter results and handles filtered API errors', async () => {
    const slowItem = buildFixtureRecord(1, {
      raw_prompt: 'Eski yavaş sorgu sonucu Alpha',
      target_mode: 'openai',
    })
    const fastItem = buildFixtureRecord(2, {
      raw_prompt: 'Yeni hızlı sorgu sonucu Beta',
      target_mode: 'anthropic',
    })

    server.use(
      http.get('/api/v1/prompt-requests', async ({ request }) => {
        const url = new URL(request.url)
        const mode = url.searchParams.get('target_mode')

        if (mode === 'openai') {
          await delay(180)
          return HttpResponse.json({
            items: [slowItem],
            total: 1,
            page: 1,
            page_size: 10,
          })
        }

        if (mode === 'anthropic') {
          await delay(20)
          return HttpResponse.json({
            items: [fastItem],
            total: 1,
            page: 1,
            page_size: 10,
          })
        }

        return HttpResponse.json({
          items: [fastItem, slowItem],
          total: 2,
          page: 1,
          page_size: 10,
        })
      }),
    )

    const user = userEvent.setup()
    const raceView = renderAppAtRoutes(['/history'])
    expect(await screen.findByText('Yeni hızlı sorgu sonucu Beta')).toBeInTheDocument()

    const targetGroup = screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' })

    // Trigger slow request (openai), then immediately switch to fast request (anthropic)
    await user.click(within(targetGroup).getByRole('button', { name: 'ChatGPT / GPT' }))
    await user.click(within(targetGroup).getByRole('button', { name: 'Claude' }))

    expect(await screen.findByText('Yeni hızlı sorgu sonucu Beta')).toBeInTheDocument()

    // Wait past slow request's 180ms completion window and verify stale openai result NEVER overwrites Claude result
    await delay(220)
    expect(screen.getByText('Yeni hızlı sorgu sonucu Beta')).toBeInTheDocument()
    expect(screen.queryByText('Eski yavaş sorgu sonucu Alpha')).not.toBeInTheDocument()
    raceView.unmount()

    // Filtered Problem Details 422 + network retry check while preserving search input text
    let networkAttempts = 0
    server.use(
      http.get('/api/v1/prompt-requests', ({ request }) => {
        const url = new URL(request.url)
        if (url.searchParams.get('q') === 'HataTesti') {
          networkAttempts += 1
          if (networkAttempts <= 2) {
            return HttpResponse.error()
          }
        }
        return HttpResponse.json({
          items: [fastItem],
          total: 1,
          page: 1,
          page_size: 10,
        })
      }),
    )

    const errorView = renderAppAtRoutes(['/history?q=HataTesti'])

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Backend servisine bağlanılamadı/i)
    // User's search text remains intact during error state
    expect(screen.getByLabelText('Anahtar Kelime ile Ara')).toHaveValue('HataTesti')

    await user.click(within(alert).getByRole('button', { name: 'Tekrar Dene' }))
    expect(await screen.findByText('Yeni hızlı sorgu sonucu Beta')).toBeInTheDocument()
    errorView.unmount()

    // Problem Details 422 on search parameter displays localized Turkish message + Request ID without SQL leak
    server.use(
      http.get('/api/v1/prompt-requests', () => {
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Unprocessable Content',
            status: 422,
            detail: 'SELECT * FROM prompt_requests WHERE raw_prompt ILIKE ...',
            request_id: 'req-search-422-id',
            error_code: 'validation_error',
            errors: [
              {
                field: 'q',
                code: 'string_too_long',
                message: 'String should have at most 200 characters',
              },
            ],
          },
          {
            status: 422,
            headers: {
              'Content-Type': 'application/problem+json',
              'X-Request-ID': 'req-search-422-id',
            },
          },
        )
      }),
    )

    renderAppAtRoutes(['/history?q=ProblemTest'])
    const problemAlert = await screen.findByRole('alert')
    expect(problemAlert).toHaveTextContent('req-search-422-id')
    expect(problemAlert).toHaveTextContent(
      'Arama Sorgusu izin verilen azami karakter sınırını aşıyor.',
    )
    expect(problemAlert).not.toHaveTextContent(/SELECT \*/i)
  })
})
