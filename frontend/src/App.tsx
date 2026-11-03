import { AppHeader } from './components/AppHeader'
import { ArchitecturePillars } from './components/ArchitecturePillars'
import { OptimizationWorkspacePreview } from './components/OptimizationWorkspacePreview'
import { StatusBanner } from './components/StatusBanner'

export default function App() {
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <AppHeader />

      <main className="mx-auto w-full max-w-6xl flex-1 space-y-8 px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
        <section className="max-w-3xl space-y-3">
          <p className="text-xs font-semibold uppercase tracking-widest text-indigo-400">
            Structured Prompt Engineering & Compilation
          </p>
          <h1 className="text-2xl font-bold tracking-tight text-slate-50 sm:text-3xl lg:text-4xl">
            Transform raw requests into evaluated, target-ready AI prompts.
          </h1>
          <p className="text-sm leading-relaxed text-slate-300 sm:text-base">
            PromptForge AI is a modular multi-agent platform designed to analyze user intent,
            construct a structured canonical prompt, evaluate quality through an independent critic,
            and compile deterministic variants for Universal, ChatGPT / GPT, Claude, and Gemini
            workflows.
          </p>
        </section>

        <StatusBanner />
        <OptimizationWorkspacePreview />
        <ArchitecturePillars />
      </main>

      <footer className="border-t border-slate-900 bg-slate-950 py-5">
        <div className="mx-auto flex max-w-6xl flex-col justify-between gap-2 px-4 text-xs text-slate-500 sm:flex-row sm:px-6 lg:px-8">
          <span>PromptForge AI · Modular Monolith Foundation (Milestone 2A)</span>
          <span>Backend Health Endpoint: GET /api/v1/health</span>
        </div>
      </footer>
    </div>
  )
}

