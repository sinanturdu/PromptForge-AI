export function StatusBanner() {
  return (
    <section
      aria-label="Implementation status notice"
      className="rounded-xl border border-indigo-500/30 bg-indigo-950/30 p-4 sm:p-5"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <p className="text-sm font-semibold text-indigo-200">
            Foundation Shell Active — AI Agent Execution Planned for Upcoming Milestones
          </p>
          <p className="text-sm leading-relaxed text-slate-300">
            This release establishes the monorepo structure, FastAPI health service, and frontend
            application shell. LLM providers, Prompt Engineer generation, independent Prompt Critic
            scoring, and automated revision workflows are intentionally not active yet and will be
            implemented across Milestones 5 through 8.6.
          </p>
        </div>
        <span className="shrink-0 self-start rounded-md border border-indigo-400/30 bg-indigo-500/15 px-3 py-1 text-xs font-medium text-indigo-200 sm:self-center">
          No Live LLM Calls
        </span>
      </div>
    </section>
  )
}

