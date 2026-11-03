import { Link, NavLink } from 'react-router'
import { ROUTES } from '../app/routes'

export function AppHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-3.5 sm:px-6 lg:px-8">
        <div className="flex w-full flex-col items-start justify-between gap-4 sm:flex-row sm:items-center sm:gap-6">
          <Link
            to={ROUTES.newPrompt}
            className="group flex items-center gap-3 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            <div
              aria-hidden="true"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-indigo-500/40 bg-indigo-500/15 font-semibold tracking-tight text-indigo-300 transition-colors group-hover:border-indigo-400/60 group-hover:bg-indigo-500/25"
            >
              PF
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-semibold tracking-tight text-slate-100">
                  PromptForge AI
                </span>
              </div>
            </div>
          </Link>

          <nav aria-label="Ana gezinme" className="flex flex-wrap items-center gap-1.5">
            <NavLink
              to={ROUTES.newPrompt}
              end
              className={({ isActive }) =>
                `inline-flex min-h-11 items-center rounded-xl px-4 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${
                  isActive
                    ? 'border border-indigo-500/40 bg-indigo-500/15 text-indigo-200'
                    : 'border border-transparent text-slate-300 hover:bg-slate-900 hover:text-slate-100'
                }`
              }
            >
              Yeni Prompt
            </NavLink>

            <NavLink
              to={ROUTES.promptHistory}
              className={({ isActive }) =>
                `inline-flex min-h-11 items-center rounded-xl px-4 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${
                  isActive
                    ? 'border border-indigo-500/40 bg-indigo-500/15 text-indigo-200'
                    : 'border border-transparent text-slate-300 hover:bg-slate-900 hover:text-slate-100'
                }`
              }
            >
              Prompt Geçmişi
            </NavLink>
          </nav>
        </div>


      </div>
    </header>
  )
}
