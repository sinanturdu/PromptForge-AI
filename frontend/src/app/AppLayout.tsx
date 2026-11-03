import { Outlet } from 'react-router'
import { AppHeader } from '../components/AppHeader'

export function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <AppHeader />

      <a href="#main-content" className="sr-only rounded-xl bg-indigo-600 p-3 text-white focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50">Ana içeriğe geç</a>
      <main id="main-content" tabIndex={-1} className="mx-auto w-full min-w-0 max-w-6xl flex-1 space-y-8 px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
        <Outlet />
      </main>

      <footer className="border-t border-slate-900 bg-slate-950 py-5">
        <div className="mx-auto flex max-w-6xl flex-col justify-between gap-2 px-4 text-xs text-slate-400 sm:flex-row sm:px-6 lg:px-8">
          <span>PromptForge AI</span>
          <span>Promptlarını kaydet, düzenli tut ve yeniden görüntüle.</span>
        </div>
      </footer>
    </div>
  )
}
