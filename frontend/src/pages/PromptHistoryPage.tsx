import { useEffect } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ROUTES } from '../app/routes'
import { ErrorAlert } from '../components/common/ErrorAlert'
import { PromptHistoryList } from '../features/prompt-requests/components/PromptHistoryList'
import { calculateTotalPages, parseHistoryPageParam } from '../features/prompt-requests/formatters'
import { usePromptRequestsQuery } from '../features/prompt-requests/hooks'
import {
  DEFAULT_HISTORY_PAGE,
  DEFAULT_HISTORY_PAGE_SIZE,
} from '../features/prompt-requests/types'

export function PromptHistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const parsedPage = parseHistoryPageParam(searchParams.get('page'))

  const { data, error, isPending, isFetching, refetch } = usePromptRequestsQuery(
    parsedPage.page,
    DEFAULT_HISTORY_PAGE_SIZE,
  )

  const totalPages = data ? calculateTotalPages(data.total, data.page_size) : 0
  const maxValidPage = Math.max(DEFAULT_HISTORY_PAGE, totalPages)
  const isPageOutOfRange = data !== undefined && parsedPage.page > maxValidPage

  useEffect(() => {
    if (parsedPage.isInvalidParam) {
      setSearchParams({}, { replace: true })
      return
    }
    if (isPageOutOfRange) {
      setSearchParams(
        maxValidPage <= 1 ? {} : { page: String(maxValidPage) },
        { replace: true },
      )
    }
  }, [isPageOutOfRange, maxValidPage, parsedPage.isInvalidParam, setSearchParams])

  function handlePageChange(nextPage: number): void {
    const safeNextPage = Math.max(DEFAULT_HISTORY_PAGE, Math.trunc(nextPage))
    if (safeNextPage <= 1) {
      setSearchParams({})
      return
    }
    setSearchParams({ page: String(safeNextPage) })
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 border-b border-slate-800/80 pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="max-w-2xl space-y-1.5">
          <p className="text-xs font-semibold uppercase tracking-widest text-indigo-400">
            Kayıtlı İstek Arşivi
          </p>
          <h1 className="text-2xl font-bold tracking-tight text-slate-50 sm:text-3xl">
            Prompt Geçmişi
          </h1>
          <p className="text-sm leading-relaxed text-slate-300">
            Daha önce kaydettiğiniz ham prompt isteklerini, hedef yapay zekâ ailesi seçimlerini ve
            detay tercihlerini inceleyin.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {data && !isPageOutOfRange ? (
            <span
              aria-label="Toplam kayıt sayısı"
              className="inline-flex items-center rounded-xl border border-slate-800 bg-slate-900/80 px-3.5 py-2 text-xs font-medium text-slate-200"
            >
              Toplam{' '}
              <span className="mx-1 font-semibold text-indigo-300">
                {data.total.toLocaleString('tr-TR')}
              </span>{' '}
              kayıt
            </span>
          ) : null}

          <Link
            to={ROUTES.newPrompt}
            className="inline-flex items-center rounded-xl bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            + Yeni Prompt Oluştur
          </Link>
        </div>
      </header>

      {isPending || isPageOutOfRange ? (
        <section
          role="status"
          aria-live="polite"
          className="space-y-4 rounded-2xl border border-slate-800/90 bg-slate-900/50 p-5 sm:p-6"
        >
          <p className="text-sm font-medium text-slate-300">
            {parsedPage.page > 1 && !isPageOutOfRange
              ? `Prompt geçmişi yükleniyor (Sayfa ${parsedPage.page})...`
              : 'Prompt geçmişi yükleniyor...'}
          </p>
          <div className="space-y-4">
            <div className="h-32 animate-pulse rounded-xl bg-slate-950/75" />
            <div className="h-32 animate-pulse rounded-xl bg-slate-950/75" />
            <div className="h-32 animate-pulse rounded-xl bg-slate-950/75" />
          </div>
        </section>
      ) : error || !data ? (
        <ErrorAlert
          title="Prompt geçmişi yüklenemedi"
          message={
            error?.userMessage ?? 'Prompt geçmişi yüklenirken beklenmeyen bir sorun oluştu.'
          }
          validationIssues={error?.validationIssues}
          requestId={error?.requestId}
          actions={
            <button
              type="button"
              onClick={() => {
                void refetch()
              }}
              className="rounded-lg border border-rose-400/40 bg-rose-950/60 px-3.5 py-1.5 text-xs font-medium text-rose-100 transition-colors hover:bg-rose-900/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
            >
              Tekrar Dene
            </button>
          }
        />
      ) : data.total === 0 ? (
        <section
          aria-label="Boş prompt geçmişi"
          className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-900/40 px-6 py-14 text-center"
        >
          <div
            aria-hidden="true"
            className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-indigo-500/30 bg-indigo-500/10 text-lg font-semibold text-indigo-300"
          >
            PF
          </div>
          <h2 className="text-base font-semibold text-slate-100 sm:text-lg">
            Henüz kayıtlı prompt isteğiniz bulunmuyor.
          </h2>
          <p className="mt-2 max-w-md text-xs leading-relaxed text-slate-400 sm:text-sm">
            Yeni bir ham prompt isteği oluşturduğunuzda kayıtlarınız burada en yeniden en eskiye
            doğru listelenir.
          </p>
          <div className="mt-6">
            <Link
              to={ROUTES.newPrompt}
              className="inline-flex items-center rounded-xl bg-indigo-600 px-5 py-2.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            >
              İlk Promptunu Oluştur
            </Link>
          </div>
        </section>
      ) : (
        <PromptHistoryList
          items={data.items}
          currentPage={data.page}
          totalPages={totalPages}
          totalItems={data.total}
          pageSize={data.page_size}
          isFetching={isFetching}
          onPageChange={handlePageChange}
        />
      )}
    </div>
  )
}
