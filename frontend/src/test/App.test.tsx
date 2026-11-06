import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'

describe('PromptForge AI application shell and focused prompt workspace', () => {
  it('renders branding, an approachable Turkish heading, and saving guidance', () => {
    render(<App />)
    expect(screen.getAllByText('PromptForge AI').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { level: 1, name: 'Yeni bir prompt oluştur' })).toBeInTheDocument()
    expect(screen.getByText(/İsteğini yaz, hedef yapay zekâyı ve detay tercihlerini belirle/)).toBeInTheDocument()
    const navigation = screen.getByRole('navigation', { name: 'Ana gezinme' })
    expect(within(navigation).getByRole('link', { name: 'Yeni Prompt' })).toHaveAttribute('aria-current', 'page')
    expect(within(navigation).getByRole('link', { name: 'Prompt Geçmişi' })).toHaveAttribute('href', '/history')
    expect(screen.getByRole('link', { name: 'Ana içeriğe geç' })).toHaveAttribute('href', '#main-content')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content')
  })

  it('explains the current limitation without exposing development status or future architecture', () => {
    render(<App />)
    expect(screen.getByText(/Kaydetmek optimizasyon başlatmaz/)).toBeInTheDocument()
    expect(screen.queryByText(/Milestone/)).not.toBeInTheDocument()
    expect(screen.queryByText('v0.0.1')).not.toBeInTheDocument()
    expect(screen.queryByText('Temel Mimari Güvenceler')).not.toBeInTheDocument()
    expect(screen.queryByText(/Sınırlandırılmış Değerlendirme ve Revizyon Akışı/)).not.toBeInTheDocument()
    expect(screen.queryByText(/POST \/api/)).not.toBeInTheDocument()
  })

  it('keeps target families and enabled submission controls in the primary form', () => {
    render(<App />)
    const form = screen.getByRole('form', { name: 'Prompt gönderim formu' })
    expect(within(form).getByLabelText(/Ham Prompt İsteği/)).toBeEnabled()
    for (const name of ['Universal', 'ChatGPT / GPT', 'Claude', 'Gemini']) {
      expect(within(form).getByRole('radio', { name: new RegExp(name.replace('/', '\\/')) })).toBeEnabled()
    }
    expect(within(form).getByRole('heading', { name: 'Detay tercihleri' })).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Prompt İsteğini Kaydet' })).toBeEnabled()
  })
})
