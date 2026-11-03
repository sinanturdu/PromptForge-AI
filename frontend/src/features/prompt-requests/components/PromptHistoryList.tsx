import type { PromptRequestRecord } from '../types'
import { PromptHistoryCard } from './PromptHistoryCard'
import { PromptHistoryPagination } from './PromptHistoryPagination'

interface PromptHistoryListProps {
  readonly items: readonly PromptRequestRecord[]
  readonly currentPage: number
  readonly totalPages: number
  readonly totalItems: number
  readonly pageSize: number
  readonly returnHistoryPath?: string
  readonly isFetching?: boolean
  readonly onPageChange: (nextPage: number) => void
}

export function PromptHistoryList({
  items,
  currentPage,
  totalPages,
  totalItems,
  pageSize,
  returnHistoryPath,
  isFetching = false,
  onPageChange,
}: PromptHistoryListProps) {
  return (
    <div className="space-y-6">
      {isFetching ? (
        <p role="status" aria-live="polite" className="text-xs font-medium text-indigo-300">
          Sayfa verileri güncelleniyor...
        </p>
      ) : null}

      <ul aria-label="Kaydedilen prompt istekleri listesi" className="space-y-4">
        {items.map((record) => (
          <li key={record.id}>
            <PromptHistoryCard
              record={record}
              currentPage={currentPage}
              returnHistoryPath={returnHistoryPath}
            />
          </li>
        ))}
      </ul>

      <PromptHistoryPagination
        currentPage={currentPage}
        totalPages={totalPages}
        totalItems={totalItems}
        pageSize={pageSize}
        isFetching={isFetching}
        onPageChange={onPageChange}
      />
    </div>
  )
}
