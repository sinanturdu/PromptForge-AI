import {
  EVALUATION_ROUND_STAGES,
  PLANNED_CONTROL_GROUPS,
  TARGET_MODE_OPTIONS,
} from '../config/platformPreview'

export function OptimizationWorkspacePreview() {
  return (
    <section aria-labelledby="workspace-preview-heading" className="space-y-4">
      <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-end">
        <div>
          <h2
            id="workspace-preview-heading"
            className="text-lg font-semibold tracking-tight text-slate-100"
          >
            Prompt Optimization Workspace (Planned Interface Slot)
          </h2>
          <p className="text-sm text-slate-400">
            Read-only structural placeholder for the upcoming compilation and multi-round evaluation
            workflow.
          </p>
        </div>
        <span className="self-start rounded-md border border-slate-800 bg-slate-900 px-2.5 py-1 text-xs font-medium text-slate-400">
          Interactive controls unlock in M4–M8.6
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-12">
        <div
          aria-disabled="true"
          className="space-y-5 rounded-xl border border-slate-800 bg-slate-900/60 p-5 lg:col-span-7"
        >
          <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
            <span className="text-sm font-medium text-slate-200">1. Raw Request & Target Mode</span>
            <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-400">
              Placeholder
            </span>
          </div>

          <div className="space-y-2">
            <span className="block text-xs font-medium uppercase tracking-wider text-slate-400">
              Target AI Mode (Planned · M6 / M8.5)
            </span>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {TARGET_MODE_OPTIONS.map((target) => (
                <div
                  key={target.id}
                  title={target.summary}
                  className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-left opacity-80"
                >
                  <div className="text-xs font-medium text-slate-200">{target.label}</div>
                  <div className="mt-0.5 text-[11px] text-slate-500">
                    {target.id} · {target.plannedMilestone}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <label
              htmlFor="raw-prompt-placeholder"
              className="block text-xs font-medium uppercase tracking-wider text-slate-400"
            >
              Raw User Request (Disabled in Milestone 2A)
            </label>
            <textarea
              id="raw-prompt-placeholder"
              disabled
              rows={4}
              placeholder="Prompt submission, ambiguity detection, and CanonicalPrompt generation will be enabled once backend persistence (M3) and Prompt Engineer (M6) are connected."
              className="w-full cursor-not-allowed resize-none rounded-lg border border-slate-800 bg-slate-950/80 p-3 text-sm text-slate-400 placeholder:text-slate-500"
            />
          </div>

          <div className="space-y-2.5">
            <span className="block text-xs font-medium uppercase tracking-wider text-slate-400">
              Optimization Controls (Planned · M8.6)
            </span>
            <div className="grid gap-3 sm:grid-cols-3">
              {PLANNED_CONTROL_GROUPS.map((group) => (
                <div
                  key={group.id}
                  className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"
                >
                  <div className="text-xs font-medium text-slate-300">{group.label}</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {group.options.map((option) => (
                      <span
                        key={option}
                        className={
                          option === group.defaultOption
                            ? 'rounded border border-indigo-500/40 bg-indigo-500/10 px-1.5 py-0.5 text-[11px] font-medium text-indigo-300'
                            : 'rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-[11px] text-slate-500'
                        }
                      >
                        {option}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div
          aria-disabled="true"
          className="flex flex-col justify-between space-y-5 rounded-xl border border-slate-800 bg-slate-900/60 p-5 lg:col-span-5"
        >
          <div className="space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
              <span className="text-sm font-medium text-slate-200">
                2. Bounded Evaluation & Revision Pipeline
              </span>
              <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-400">
                Max 3 Rounds
              </span>
            </div>

            <ol className="space-y-3">
              {EVALUATION_ROUND_STAGES.map((stage) => (
                <li
                  key={stage.round}
                  className="rounded-lg border border-slate-800/90 bg-slate-950/70 p-3"
                >
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-slate-700 bg-slate-900 text-xs font-semibold text-slate-300">
                      {stage.round}
                    </span>
                    <span className="text-xs font-semibold text-slate-200">{stage.title}</span>
                  </div>
                  <p className="mt-1.5 pl-7 text-xs leading-relaxed text-slate-400">
                    {stage.description}
                  </p>
                </li>
              ))}
            </ol>
          </div>

          <div className="rounded-lg border border-dashed border-slate-700/80 bg-slate-950/60 p-3.5">
            <div className="text-xs font-medium text-slate-300">
              Compiled Output & Evaluation Report Slot
            </div>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              Final rendered prompt, deterministic rubric score out of 100, version history, and
              one-click copy controls will appear here in Milestones 6–8.5.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}

