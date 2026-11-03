export function AppHeader() {
  return (
    <header className="border-b border-slate-800/80 bg-slate-950/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <div
            aria-hidden="true"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-indigo-500/40 bg-indigo-500/10 font-semibold tracking-tight text-indigo-300"
          >
            PF
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <span className="text-base font-semibold tracking-tight text-slate-100">
                PromptForge AI
              </span>
              <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 text-xs font-medium text-slate-300">
                v0.0.1
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Modular Multi-Agent Prompt Engineering Platform
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 font-medium text-amber-200">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden="true" />
            Milestone 2A Foundation
          </span>
        </div>
      </div>
    </header>
  )
}

