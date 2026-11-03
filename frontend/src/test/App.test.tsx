import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'

describe('PromptForge AI initial foundation shell', () => {
  it('renders PromptForge AI branding, title, and platform description', () => {
    render(<App />)

    expect(screen.getByText('PromptForge AI')).toBeInTheDocument()
    expect(
      screen.getByRole('heading', {
        level: 1,
        name: /Transform raw requests into evaluated, target-ready AI prompts/i,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText(/modular multi-agent platform/i)).toBeInTheDocument()
  })

  it('clearly indicates that AI agent execution will be implemented in later milestones', () => {
    render(<App />)

    expect(
      screen.getByText(
        /Foundation Shell Active — AI Agent Execution Planned for Upcoming Milestones/i,
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('No Live LLM Calls')).toBeInTheDocument()
  })

  it('renders the disabled workspace placeholder and planned target modes without fake actions', () => {
    render(<App />)

    const promptInput = screen.getByLabelText(/Raw User Request/i)
    expect(promptInput).toBeDisabled()

    expect(screen.getByText('Universal')).toBeInTheDocument()
    expect(screen.getByText('ChatGPT / GPT')).toBeInTheDocument()
    expect(screen.getByText('Claude')).toBeInTheDocument()
    expect(screen.getByText('Gemini')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

