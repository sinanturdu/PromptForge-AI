import { Outlet } from 'react-router'
import { AppHeader } from '../components/AppHeader'

export function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <AppHeader />

      <main className="mx-auto w-full max-w-6xl flex-1 space-y-8 px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
        <Outlet />
      </main>

      <footer className="border-t border-slate-900 bg-slate-950 py-5">
        <div className="mx-auto flex max-w-6xl flex-col justify-between gap-2 px-4 text-xs text-slate-500 sm:flex-row sm:px-6 lg:px-8">
          <span>PromptForge AI · Modüler Monolit Altyapısı (Milestone 4A)</span>
          <span>Backend API: /api/v1/prompt-requests · /api/v1/health</span>
        </div>
      </footer>
    </div>
  )
}
