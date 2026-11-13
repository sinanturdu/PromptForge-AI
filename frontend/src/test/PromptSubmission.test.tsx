import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, delay, http } from 'msw'
import { describe, expect, it } from 'vitest'
import {
  MAX_RAW_PROMPT_LENGTH,
  MAX_USER_CONTEXT_LENGTH,
} from '../features/prompt-requests/types'
import { buildApiV1Url, resolveApiBaseUrl } from '../lib/api-client'
import { getRecordedGetIds, getRecordedPostPayloads } from './msw/handlers'
import { server } from './msw/server'
import { renderAppAtRoutes } from './renderWithApp'

describe('Prompt submission form and backend integration (Milestone 4A)', () => {
  it('renders all required form inputs, default selections, and character counters', () => {
    renderAppAtRoutes(['/'])

    expect(screen.getByLabelText(/Ham Prompt İsteği/i)).toHaveValue('')
    expect(screen.getByLabelText(/Proje Bağlamı/i)).toHaveValue('')
    expect(screen.getByLabelText(/Çıktı Dili/i)).toHaveValue('tr')
    expect(screen.getByLabelText(/Görev Profili/i)).toHaveValue('')

    const targetGroup = screen.getByRole('group', { name: /Hedef Yapay Zekâ Ailesi/i })
    expect(within(targetGroup).getByRole('radio', { name: /Universal/i })).toBeChecked()

    const promptDetailGroup = screen.getByRole('group', { name: /Prompt Detay Seviyesi/i })
    expect(within(promptDetailGroup).getByRole('radio', { name: /Orta/i })).toBeChecked()

    const responseDetailGroup = screen.getByRole('group', {
      name: /Beklenen Yanıt Detay Tercihi/i,
    })
    expect(within(responseDetailGroup).getByRole('radio', { name: /Dengeli/i })).toBeChecked()

    const reasoningGroup = screen.getByRole('group', { name: /Akıl Yürütme Tercihi/i })
    expect(within(reasoningGroup).getByRole('radio', { name: /Otomatik/i })).toBeChecked()

    expect(screen.getAllByText(/0 \/ 20\.000/)).toHaveLength(2)
    expect(screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i })).toBeEnabled()
  })

  it('supports selecting target AI family, prompt detail, response detail, and reasoning preferences', async () => {
    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    const targetGroup = screen.getByRole('group', { name: /Hedef Yapay Zekâ Ailesi/i })
    const claudeRadio = within(targetGroup).getByRole('radio', { name: /Claude/i })
    await user.click(claudeRadio)
    expect(claudeRadio).toBeChecked()

    const geminiRadio = within(targetGroup).getByRole('radio', { name: /Gemini/i })
    await user.click(geminiRadio)
    expect(geminiRadio).toBeChecked()

    const promptDetailGroup = screen.getByRole('group', { name: /Prompt Detay Seviyesi/i })
    const highPromptDetail = within(promptDetailGroup).getByRole('radio', { name: /Yüksek/i })
    await user.click(highPromptDetail)
    expect(highPromptDetail).toBeChecked()

    const responseDetailGroup = screen.getByRole('group', {
      name: /Beklenen Yanıt Detay Tercihi/i,
    })
    const comprehensiveResponse = within(responseDetailGroup).getByRole('radio', {
      name: /Kapsamlı/i,
    })
    await user.click(comprehensiveResponse)
    expect(comprehensiveResponse).toBeChecked()

    const reasoningGroup = screen.getByRole('group', { name: /Akıl Yürütme Tercihi/i })
    const highReasoning = within(reasoningGroup).getByRole('radio', { name: /Yüksek/i })
    await user.click(highReasoning)
    expect(highReasoning).toBeChecked()

    // Typing or changing controls must never auto-submit the form.
    expect(getRecordedPostPayloads()).toHaveLength(0)
  })

  it('describes current renderer support and keeps all four targets keyboard-selectable', async () => {
    const user = userEvent.setup()
    renderAppAtRoutes(['/'])
    const targetGroup = within(screen.getByRole('group', { name: 'Hedef Yapay Zekâ Ailesi' }))
    const universal = targetGroup.getByRole('radio', { name: 'Universal' })
    const openai = targetGroup.getByRole('radio', { name: 'ChatGPT / GPT' })
    const anthropic = targetGroup.getByRole('radio', { name: 'Claude' })
    const google = targetGroup.getByRole('radio', { name: 'Gemini' })
    expect(universal).toHaveAccessibleDescription(/GenericPromptRenderer/)
    expect(openai).toHaveAccessibleDescription(/Yeni iş akışları OpenAIPromptRenderer/)
    expect(anthropic).toHaveAccessibleDescription(/GenericPromptRenderer.*özel biçimlendirme planlanıyor/)
    expect(google).toHaveAccessibleDescription(/GenericPromptRenderer.*özel biçimlendirme planlanıyor/)
    universal.focus()
    for (const radio of [openai, anthropic, google, universal]) {
      await user.keyboard('{ArrowRight}')
      expect(radio).toHaveFocus()
      expect(radio).toBeChecked()
    }
    expect(getRecordedPostPayloads()).toHaveLength(0)
  })

  it('rejects empty and whitespace-only raw prompts without calling the backend API', async () => {
    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    const submitButton = screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i })
    await user.click(submitButton)

    expect(screen.getByText('Ham prompt alanı zorunludur.')).toBeInTheDocument()
    expect(getRecordedPostPayloads()).toHaveLength(0)

    const rawPromptInput = screen.getByLabelText(/Ham Prompt İsteği/i)
    await user.type(rawPromptInput, '   \n\t  ')
    await user.click(submitButton)

    expect(
      screen.getByText('Ham prompt yalnızca boşluk veya satır sonu karakterlerinden oluşamaz.'),
    ).toBeInTheDocument()
    expect(getRecordedPostPayloads()).toHaveLength(0)
  })

  it('enforces the 20,000 character limit on raw prompt and user context', async () => {
    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    const rawPromptInput = screen.getByLabelText(/Ham Prompt İsteği/i)
    const contextInput = screen.getByLabelText(/Proje Bağlamı/i)

    fireEvent.change(rawPromptInput, {
      target: { value: 'A'.repeat(MAX_RAW_PROMPT_LENGTH + 5) },
    })
    fireEvent.change(contextInput, {
      target: { value: 'B'.repeat(MAX_USER_CONTEXT_LENGTH + 2) },
    })

    await user.click(screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i }))

    expect(
      screen.getByText(/Ham prompt en fazla 20\.000 karakter olabilir/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Proje bağlamı en fazla 20\.000 karakter olabilir/i),
    ).toBeInTheDocument()
    expect(getRecordedPostPayloads()).toHaveLength(0)
  })

  it('submits valid multiline JSON to POST /api/v1/prompt-requests, redirects on 201, and loads detail via GET by UUID', async () => {
    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    const multilinePrompt =
      'React ve FastAPI ile profesyonel bir görev yönetim platformu tasarla.\n- Ekip yetkilendirmesi\n- Proje panoları'
    const multilineContext = 'Backend SQLite + SQLAlchemy Async kullanıyor.\nArayüz dili Türkçe.'

    fireEvent.change(screen.getByLabelText(/Ham Prompt İsteği/i), {
      target: { value: multilinePrompt },
    })
    fireEvent.change(screen.getByLabelText(/Proje Bağlamı/i), {
      target: { value: multilineContext },
    })

    await user.selectOptions(screen.getByLabelText(/Çıktı Dili/i), 'en')
    await user.click(screen.getByRole('button', { name: 'Yazılım Geliştirme' }))

    const targetGroup = screen.getByRole('group', { name: /Hedef Yapay Zekâ Ailesi/i })
    await user.click(within(targetGroup).getByRole('radio', { name: /ChatGPT \/ GPT/i }))

    const promptDetailGroup = screen.getByRole('group', { name: /Prompt Detay Seviyesi/i })
    await user.click(within(promptDetailGroup).getByRole('radio', { name: /Yüksek/i }))

    const responseDetailGroup = screen.getByRole('group', {
      name: /Beklenen Yanıt Detay Tercihi/i,
    })
    await user.click(within(responseDetailGroup).getByRole('radio', { name: /Kapsamlı/i }))

    const reasoningGroup = screen.getByRole('group', { name: /Akıl Yürütme Tercihi/i })
    await user.click(within(reasoningGroup).getByRole('radio', { name: /Yüksek/i }))

    await user.click(screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i }))

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { level: 1, name: /Prompt İsteği Detayları/i }),
      ).toBeInTheDocument()
    })

    expect(getRecordedPostPayloads()).toEqual([
      {
        raw_prompt: multilinePrompt,
        user_context: multilineContext,
        output_language: 'en',
        target_mode: 'openai',
        task_profile: 'software_development',
        prompt_detail_level: 'high',
        response_detail_preference: 'comprehensive',
        reasoning_preference: 'high',
      },
    ])

    const expectedCreatedId = '550e8400-e29b-41d4-a716-446655440001'
    expect(getRecordedGetIds()).toContain(expectedCreatedId)

    expect(screen.getByText(/Prompt isteğiniz başarıyla kaydedildi/i)).toBeInTheDocument()
    expect(screen.getByText(expectedCreatedId)).toBeInTheDocument()
    expect(screen.getByText(/React ve FastAPI ile profesyonel bir görev yönetim/i)).toBeInTheDocument()
    expect(screen.getByText('İngilizce (en)')).toBeInTheDocument()
    expect(screen.getByText('software_development')).toBeInTheDocument()
  })

  it('displays backend 422 Problem Details validation issues, shows X-Request-ID, and preserves entered text', async () => {
    server.use(
      http.post('/api/v1/prompt-requests', () => {
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Unprocessable Content',
            status: 422,
            detail: 'Request validation failed.',
            instance: '/api/v1/prompt-requests',
            request_id: 'req-422-validation-trace',
            error_code: 'validation_error',
            errors: [
              {
                field: 'task_profile',
                code: 'string_too_long',
                message: 'Input exceeds maximum length of 64 characters.',
              },
            ],
          },
          {
            status: 422,
            headers: {
              'Content-Type': 'application/problem+json',
              'X-Request-ID': 'req-422-validation-trace',
            },
          },
        )
      }),
    )

    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    const preservedPrompt = 'Bu prompt metni hata sonrasında kaybolmamalıdır.'
    const preservedContext = 'Korunması gereken proje bağlamı.'

    await user.type(screen.getByLabelText(/Ham Prompt İsteği/i), preservedPrompt)
    await user.type(screen.getByLabelText(/Proje Bağlamı/i), preservedContext)
    await user.click(screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/doğrulama hatası bulundu/i)
    expect(alert).toHaveTextContent('req-422-validation-trace')
    expect(alert).toHaveTextContent(/Görev Profili:.*izin verilen azami karakter sınırını aşıyor/i)

    expect(screen.getByLabelText(/Ham Prompt İsteği/i)).toHaveValue(preservedPrompt)
    expect(screen.getByLabelText(/Proje Bağlamı/i)).toHaveValue(preservedContext)
  })

  it('shows safe user-facing feedback on HTTP 500 server errors without exposing internal details and without retrying POST', async () => {
    let postCallCount = 0
    server.use(
      http.post('/api/v1/prompt-requests', () => {
        postCallCount += 1
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Internal Server Error',
            status: 500,
            detail: 'Traceback sqlite3.OperationalError: secret internal failure',
            instance: '/api/v1/prompt-requests',
            request_id: 'req-500-safe-trace',
            error_code: 'internal_server_error',
          },
          {
            status: 500,
            headers: {
              'Content-Type': 'application/problem+json',
              'X-Request-ID': 'req-500-safe-trace',
            },
          },
        )
      }),
    )

    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    const enteredPrompt = 'Sunucu 500 döndürdüğünde metin korunmalı.'
    await user.type(screen.getByLabelText(/Ham Prompt İsteği/i), enteredPrompt)
    await user.click(screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Sunucu tarafında beklenmeyen bir hata oluştu/i)
    expect(alert).toHaveTextContent('req-500-safe-trace')
    expect(alert).not.toHaveTextContent(/sqlite3/i)
    expect(alert).not.toHaveTextContent(/Traceback/i)

    expect(postCallCount).toBe(1)
    expect(screen.getByLabelText(/Ham Prompt İsteği/i)).toHaveValue(enteredPrompt)
  })

  it('disables submit button while saving and prevents accidental duplicate POST submissions', async () => {
    let postAttempts = 0
    server.use(
      http.post('/api/v1/prompt-requests', async ({ request }) => {
        postAttempts += 1
        const payload = (await request.json()) as { raw_prompt: string }
        await delay(120)
        return HttpResponse.json(
          {
            id: '550e8400-e29b-41d4-a716-446655440099',
            raw_prompt: payload.raw_prompt,
            user_context: null,
            output_language: 'tr',
            target_mode: 'generic',
            task_profile: null,
            prompt_detail_level: 'medium',
            response_detail_preference: 'balanced',
            reasoning_preference: 'auto',
            created_at: '2026-11-03T11:00:00Z',
          },
          { status: 201 },
        )
      }),
      http.get('/api/v1/prompt-requests/:id', ({ params }) => {
        return HttpResponse.json({
          id: String(params.id),
          raw_prompt: 'Çift tıklama koruması testi.',
          user_context: null,
          output_language: 'tr',
          target_mode: 'generic',
          task_profile: null,
          prompt_detail_level: 'medium',
          response_detail_preference: 'balanced',
          reasoning_preference: 'auto',
          created_at: '2026-11-03T11:00:00Z',
        })
      }),
    )

    const user = userEvent.setup()
    renderAppAtRoutes(['/'])

    await user.type(
      screen.getByLabelText(/Ham Prompt İsteği/i),
      'Çift tıklama koruması testi.',
    )

    const submitButton = screen.getByRole('button', { name: /Prompt İsteğini Kaydet/i })
    await user.dblClick(submitButton)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Kaydediliyor\.\.\./i })).toBeDisabled()
    })

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { level: 1, name: /Prompt İsteği Detayları/i }),
      ).toBeInTheDocument()
    })

    expect(postAttempts).toBe(1)
  })

  it('normalizes configurable API base URLs cleanly without hardcoding origins', () => {
    expect(resolveApiBaseUrl('')).toBe('')
    expect(buildApiV1Url('/prompt-requests', '')).toBe('/api/v1/prompt-requests')
    expect(buildApiV1Url('prompt-requests', 'http://127.0.0.1:8000/')).toBe(
      'http://127.0.0.1:8000/api/v1/prompt-requests',
    )
    expect(buildApiV1Url('/prompt-requests', 'https://api.example.com/api/v1')).toBe(
      'https://api.example.com/api/v1/prompt-requests',
    )
  })
})
