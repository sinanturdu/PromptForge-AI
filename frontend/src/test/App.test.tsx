import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'

describe('PromptForge AI application shell and preserved architectural overview', () => {
  it('renders PromptForge AI branding, Turkish hero heading, and platform description', () => {
    render(<App />)

    expect(screen.getByText('PromptForge AI')).toBeInTheDocument()
    expect(
      screen.getByRole('heading', {
        level: 1,
        name: /Ham isteklerinizi yapılandırılmış ve hedefe hazır prompt kayıtlarına dönüştürün/i,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText(/modüler çoklu ajan platformudur/i)).toBeInTheDocument()
  })

  it('clearly distinguishes active persistence from planned AI agent milestones without outdated disabled claims', () => {
    render(<App />)

    expect(
      screen.getByText(
        /Prompt Gönderimi ve Kalıcı Kayıt Aktif — AI Optimizasyon Ajanları Planlama Aşamasında/i,
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Canlı LLM Çağrısı Yok')).toBeInTheDocument()
    expect(screen.queryByText(/Disabled in Milestone 2A/i)).not.toBeInTheDocument()
  })

  it('preserves architectural guarantees, 3-round evaluation overview, and enabled submission controls', () => {
    render(<App />)

    const promptInput = screen.getByLabelText(/Ham Prompt İsteği/i)
    expect(promptInput).toBeEnabled()

    expect(screen.getByText('Universal')).toBeInTheDocument()
    expect(screen.getByText('ChatGPT / GPT')).toBeInTheDocument()
    expect(screen.getByText('Claude')).toBeInTheDocument()
    expect(screen.getByText('Gemini')).toBeInTheDocument()

    expect(screen.getByText('Temel Mimari Güvenceler')).toBeInTheDocument()
    expect(screen.getByText('Bağımsız Kalite Denetimi')).toBeInTheDocument()
    expect(screen.getByText('Deterministik 3 Tur Revizyon Sınırı')).toBeInTheDocument()
    expect(screen.getByText('CanonicalPrompt ve Hedef Derleyiciler')).toBeInTheDocument()
    expect(
      screen.getByText(/Sınırlandırılmış Değerlendirme ve Revizyon Akışı/i),
    ).toBeInTheDocument()
  })
})
