import { ARCHITECTURE_PILLARS } from '../config/platformPreview'

export function ArchitecturePillars() {
  return (
    <section aria-labelledby="architecture-pillars-heading" className="space-y-4">
      <h2
        id="architecture-pillars-heading"
        className="text-lg font-semibold tracking-tight text-slate-100"
      >
        Core Architectural Guarantees
      </h2>

      <div className="grid gap-4 md:grid-cols-3">
        {ARCHITECTURE_PILLARS.map((pillar) => (
          <article
            key={pillar.id}
            className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/50 p-5"
          >
            <div className="space-y-2">
              <span className="inline-block rounded border border-slate-800 bg-slate-950 px-2 py-0.5 text-[11px] font-medium text-indigo-300">
                {pillar.milestoneTag}
              </span>
              <h3 className="text-sm font-semibold text-slate-100">{pillar.title}</h3>
              <p className="text-sm leading-relaxed text-slate-400">{pillar.description}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}

