import {
  getTargetFamilyMeta,
  MAX_SEARCH_QUERY_LENGTH,
  TARGET_AI_FILTER_OPTIONS,
  type TargetMode,
  type TargetModeFilter,
} from '../types'

interface PromptHistoryFilterBarProps {
  readonly searchValue: string
  readonly committedQuery?: string
  readonly selectedTargetMode?: TargetMode
  readonly matchingCount?: number
  readonly isDebouncing?: boolean
  readonly showClearAllButton?: boolean
  readonly onSearchValueChange: (nextValue: string) => void
  readonly onClearSearch: () => void
  readonly onTargetModeChange: (nextFilter: TargetModeFilter) => void
  readonly onClearTargetMode: () => void
  readonly onClearAllFilters: () => void
}

export function PromptHistoryFilterBar({
  searchValue,
  committedQuery,
  selectedTargetMode,
  matchingCount,
  isDebouncing = false,
  showClearAllButton = true,
  onSearchValueChange,
  onClearSearch,
  onTargetModeChange,
  onClearTargetMode,
  onClearAllFilters,
}: PromptHistoryFilterBarProps) {
  const activeFilterValue: TargetModeFilter = selectedTargetMode ?? 'all'
  const hasCommittedFilters = Boolean(committedQuery || selectedTargetMode)
  const hasAnyClearableState = hasCommittedFilters || searchValue.trim().length > 0

  return (
    <section
      aria-label="Prompt geçmişi arama ve filtreleme"
      className="space-y-4 rounded-2xl border border-slate-800/90 bg-slate-900/55 p-4 sm:p-5"
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        {/* Keyword Search Input */}
        <div className="w-full space-y-1.5 lg:max-w-md">
          <div className="flex items-center justify-between gap-2">
            <label
              htmlFor="prompt-history-search"
              className="text-xs font-semibold tracking-wide text-slate-200"
            >
              Anahtar Kelime ile Ara
            </label>
            {isDebouncing ? (
              <span aria-live="polite" className="text-[11px] font-medium text-indigo-300">
                Aranıyor...
              </span>
            ) : null}
          </div>

          <div className="relative flex items-center">
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-3.5 text-slate-400"
            >
              <svg
                className="h-4 w-4"
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              >
                <circle cx="8.75" cy="8.75" r="5.75" />
                <path strokeLinecap="round" d="M13 13l4 4" />
              </svg>
            </span>

            <input
              id="prompt-history-search"
              type="search"
              value={searchValue}
              maxLength={MAX_SEARCH_QUERY_LENGTH}
              autoComplete="off"
              placeholder="Promptlarında ara..."
              onChange={(event) => {
                onSearchValueChange(event.target.value)
              }}
              className="w-full rounded-xl border border-slate-700/90 bg-slate-950/85 py-2.5 pr-10 pl-10 text-sm text-slate-100 placeholder:text-slate-500 transition-colors hover:border-slate-600 focus:border-indigo-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/70 [&::-webkit-search-cancel-button]:hidden"
            />

            {searchValue.length > 0 ? (
              <button
                type="button"
                aria-label="Aramayı temizle"
                onClick={onClearSearch}
                className="absolute right-2.5 inline-flex h-6 w-6 items-center justify-center rounded-lg text-xs font-semibold text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
              >
                <span aria-hidden="true">✕</span>
              </button>
            ) : null}
          </div>
        </div>

        {/* Target AI Family Segmented Control */}
        <div className="space-y-1.5">
          <span
            id="target-ai-filter-label"
            className="block text-xs font-semibold tracking-wide text-slate-200"
          >
            Hedef Yapay Zekâ Ailesi
          </span>

          <div
            role="group"
            aria-labelledby="target-ai-filter-label"
            className="flex flex-wrap items-center gap-1.5"
          >
            {TARGET_AI_FILTER_OPTIONS.map((option) => {
              const isSelected = activeFilterValue === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => {
                    onTargetModeChange(option.value)
                  }}
                  className={
                    isSelected
                      ? 'rounded-xl border border-indigo-500/60 bg-indigo-500/20 px-3 py-2 text-xs font-semibold text-indigo-100 shadow-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400'
                      : 'rounded-xl border border-slate-800 bg-slate-950/75 px-3 py-2 text-xs font-medium text-slate-300 transition-colors hover:border-slate-700 hover:bg-slate-900 hover:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400'
                  }
                >
                  {option.label}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {/* Active Filter Summary & Reset Row */}
      {hasCommittedFilters || (hasAnyClearableState && showClearAllButton) ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-800/80 pt-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {hasCommittedFilters && matchingCount !== undefined ? (
              <span
                aria-live="polite"
                className="font-medium text-slate-300"
              >
                <span className="font-semibold text-indigo-300">
                  {matchingCount.toLocaleString('tr-TR')}
                </span>{' '}
                eşleşen sonuç
              </span>
            ) : null}

            {committedQuery ? (
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/35 bg-indigo-500/10 px-2.5 py-1 text-xs text-indigo-100">
                <span className="text-indigo-300">Arama:</span>
                <span className="max-w-48 truncate font-medium">&ldquo;{committedQuery}&rdquo;</span>
                <button
                  type="button"
                  aria-label="Arama filtresini kaldır"
                  onClick={onClearSearch}
                  className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded text-[11px] text-indigo-200 transition-colors hover:bg-indigo-500/30 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
                >
                  <span aria-hidden="true">✕</span>
                </button>
              </span>
            ) : null}

            {selectedTargetMode ? (
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/35 bg-indigo-500/10 px-2.5 py-1 text-xs text-indigo-100">
                <span className="text-indigo-300">Hedef AI:</span>
                <span className="font-medium">{getTargetFamilyMeta(selectedTargetMode).label}</span>
                <button
                  type="button"
                  aria-label="Hedef model filtresini kaldır"
                  onClick={onClearTargetMode}
                  className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded text-[11px] text-indigo-200 transition-colors hover:bg-indigo-500/30 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
                >
                  <span aria-hidden="true">✕</span>
                </button>
              </span>
            ) : null}
          </div>

          {showClearAllButton ? (
            <button
              type="button"
              onClick={onClearAllFilters}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-950/80 px-3 py-1.5 text-xs font-semibold text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-800 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            >
              Filtreleri Temizle
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
