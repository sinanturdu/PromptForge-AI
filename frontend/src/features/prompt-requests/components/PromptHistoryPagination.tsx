interface PromptHistoryPaginationProps {
  readonly currentPage: number
  readonly totalPages: number
  readonly totalItems: number
  readonly pageSize: number
  readonly isFetching?: boolean
  readonly onPageChange: (nextPage: number) => void
}

export function PromptHistoryPagination({
  currentPage,
  totalPages,
  totalItems,
  pageSize,
  isFetching = false,
  onPageChange,
}: PromptHistoryPaginationProps) {
  if (totalItems <= 0 || totalPages <= 0) {
    return null
  }

  const startItem = (currentPage - 1) * pageSize + 1
  const endItem = Math.min(currentPage * pageSize, totalItems)
  const isPreviousDisabled = currentPage <= 1 || isFetching
  const isNextDisabled = currentPage >= totalPages || isFetching

  return (
    <nav
      aria-label="Prompt geçmişi sayfalama"
      className="flex flex-col gap-3 rounded-2xl border border-slate-800/90 bg-slate-900/50 p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5"
    >
      <p className="text-xs text-slate-300">
        <span className="font-semibold text-slate-100">
          {startItem.toLocaleString('tr-TR')}–{endItem.toLocaleString('tr-TR')}
        </span>{' '}
        / toplam{' '}
        <span className="font-semibold text-slate-100">{totalItems.toLocaleString('tr-TR')}</span>{' '}
        kayıt gösteriliyor
      </p>

      <div className="flex flex-wrap items-center justify-between gap-3 sm:justify-end">
        <span aria-live="polite" className="text-xs font-medium text-slate-300">
          Sayfa <span className="font-semibold text-slate-100">{currentPage}</span> /{' '}
          <span className="font-semibold text-slate-100">{totalPages}</span>
        </span>

        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={isPreviousDisabled}
            onClick={() => {
              if (!isPreviousDisabled) {
                onPageChange(currentPage - 1)
              }
            }}
            className="inline-flex items-center gap-1 rounded-xl border border-slate-700 bg-slate-900 px-3.5 py-2 text-xs font-semibold text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:border-slate-800 disabled:bg-slate-950/60 disabled:text-slate-500"
          >
            <span aria-hidden="true">←</span>
            <span>Önceki</span>
          </button>

          <button
            type="button"
            disabled={isNextDisabled}
            onClick={() => {
              if (!isNextDisabled) {
                onPageChange(currentPage + 1)
              }
            }}
            className="inline-flex items-center gap-1 rounded-xl border border-slate-700 bg-slate-900 px-3.5 py-2 text-xs font-semibold text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:border-slate-800 disabled:bg-slate-950/60 disabled:text-slate-500"
          >
            <span>Sonraki</span>
            <span aria-hidden="true">→</span>
          </button>
        </div>
      </div>
    </nav>
  )
}
