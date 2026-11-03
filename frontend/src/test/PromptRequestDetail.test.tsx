import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { getRecordedGetIds, seedMockPromptRecord } from './msw/handlers'
import { renderAppAtRoutes } from './renderWithApp'

describe('Prompt request detail page and direct deep-link refresh behavior', () => {
  it('loads saved prompt details via GET /api/v1/prompt-requests/:id without relying on navigation state and navigates back', async () => {
    const savedUuid = '9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d'
    seedMockPromptRecord({
      id: savedUuid,
      raw_prompt: 'Mikroservis log analizi için yapılandırılmış bir prompt hazırla.\nAdım adım çıktı üret.',
      user_context: 'Sistem Kubernetes üzerinde çalışıyor.',
      output_language: 'tr',
      target_mode: 'anthropic',
      task_profile: 'architecture_design',
      prompt_detail_level: 'high',
      response_detail_preference: 'balanced',
      reasoning_preference: 'medium',
      created_at: '2026-11-03T12:15:30Z',
    })

    const user = userEvent.setup()
    // Simulate direct browser load / refresh on the detail route with NO router state.
    renderAppAtRoutes([`/prompt-requests/${savedUuid}`])

    expect(screen.getByRole('status')).toHaveTextContent(/yükleniyor/i)

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { level: 1, name: /Prompt İsteği Detayları/i }),
      ).toBeInTheDocument()
    })

    expect(getRecordedGetIds()).toEqual([savedUuid])
    expect(screen.getByText(savedUuid)).toBeInTheDocument()
    expect(
      screen.getByText(/Mikroservis log analizi için yapılandırılmış bir prompt hazırla/i),
    ).toBeInTheDocument()
    expect(screen.getByText('Sistem Kubernetes üzerinde çalışıyor.')).toBeInTheDocument()
    expect(screen.getByText('Claude')).toBeInTheDocument()
    expect(screen.getByText('Türkçe (tr)')).toBeInTheDocument()
    expect(screen.getByText('architecture_design')).toBeInTheDocument()
    expect(screen.getByText('Yüksek')).toBeInTheDocument()
    expect(screen.getByText('Dengeli')).toBeInTheDocument()
    expect(screen.getByText('Orta')).toBeInTheDocument()

    // Honest non-AI message must be displayed; no fake scores or generated prompts.
    expect(
      screen.getByText(/AI Optimizasyonu İlerleyen Aşamalarda Etkinleşecek/i),
    ).toBeInTheDocument()

    // Navigate back to the new prompt page.
    await user.click(screen.getByRole('link', { name: /\+ Yeni Prompt İsteği Oluştur/i }))

    await waitFor(() => {
      expect(screen.getByLabelText(/Ham Prompt İsteği/i)).toBeInTheDocument()
    })
  })

  it('shows a clear 404 Problem Details error state with X-Request-ID when UUID does not exist', async () => {
    const missingUuid = '00000000-0000-4000-8000-000000000404'
    renderAppAtRoutes([`/prompt-requests/${missingUuid}`])

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Aradığınız prompt isteği bulunamadı/i)
    expect(alert).toHaveTextContent('req-404-missing-id')
    expect(
      screen.getByRole('link', { name: /Yeni Prompt Sayfasına Dön/i }),
    ).toBeInTheDocument()
  })

  it('renders a clean not-found route screen for unknown frontend paths', () => {
    renderAppAtRoutes(['/unknown-frontend-route'])

    expect(
      screen.getByRole('heading', { level: 1, name: /Aradığınız sayfa bulunamadı/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /Yeni Prompt Sayfasına Dön/i }),
    ).toBeInTheDocument()
  })
})
