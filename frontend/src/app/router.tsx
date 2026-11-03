import { BrowserRouter, Link, Route, Routes } from 'react-router'
import { NewPromptPage } from '../pages/NewPromptPage'
import { PromptRequestDetailPage } from '../pages/PromptRequestDetailPage'
import { AppLayout } from './AppLayout'
import { ROUTES } from './routes'

function NotFoundRouteView() {
  return (
    <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/50 p-6 sm:p-8">
      <p className="text-xs font-semibold uppercase tracking-wider text-amber-400">404</p>
      <h1 className="text-xl font-bold text-slate-100 sm:text-2xl">Aradığınız sayfa bulunamadı</h1>
      <p className="text-sm text-slate-400">
        İstediğiniz adres mevcut değil. Yeni bir prompt isteği oluşturmak için ana çalışma alanına
        dönebilirsiniz.
      </p>
      <div>
        <Link
          to={ROUTES.newPrompt}
          className="inline-flex rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-indigo-500"
        >
          Yeni Prompt Sayfasına Dön
        </Link>
      </div>
    </section>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path={ROUTES.newPrompt} element={<NewPromptPage />} />
        <Route path={ROUTES.promptRequestDetailPattern} element={<PromptRequestDetailPage />} />
        <Route path="*" element={<NotFoundRouteView />} />
      </Route>
    </Routes>
  )
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}
