import { Link } from 'react-router'
import { ROUTES } from '../../../app/routes'
import { formatPromptTimestamp } from '../formatters'
import {
  getPromptDetailMeta,
  getTargetFamilyMeta,
  type PromptRequestRecord,
} from '../types'

interface PromptHistoryCardProps {
  readonly record: PromptRequestRecord
  readonly currentPage: number
  readonly returnHistoryPath?: string
}

export function PromptHistoryCard({
  record,
  currentPage,
  returnHistoryPath,
}: PromptHistoryCardProps) {
  const targetMeta = getTargetFamilyMeta(record.target_mode)
  const promptDetailMeta = getPromptDetailMeta(record.prompt_detail_level)
  const cardHeadingId = `history-card-${record.id}`
  const resolvedHistoryPath = returnHistoryPath ?? ROUTES.promptHistoryWithPage(currentPage)

  return (
    <article
      aria-labelledby={cardHeadingId}
      className="rounded-2xl border border-slate-800/90 bg-slate-900/60 p-5 transition-colors hover:border-slate-700/90 hover:bg-slate-900/80 sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/35 bg-indigo-500/10 px-2.5 py-1 text-xs font-semibold text-indigo-200">
            <span>{targetMeta.label}</span>
            <span className="font-mono text-[11px] font-normal text-indigo-300/90">
              ({record.target_mode})
            </span>
          </span>

          <span className="inline-flex items-center gap-1 rounded-lg border border-slate-700/90 bg-slate-950/80 px-2.5 py-1 text-xs font-medium text-slate-200">
            <span className="text-slate-400">Prompt Detayı:</span>
            <span>{promptDetailMeta.label}</span>
          </span>

          {record.task_profile ? (
            <span className="inline-flex items-center rounded-lg border border-slate-700/80 bg-slate-950/60 px-2.5 py-1 font-mono text-xs text-slate-300">
              {record.task_profile}
            </span>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2.5 text-xs text-slate-400">
          <span
            id={cardHeadingId}
            title={record.id}
            className="rounded border border-slate-800 bg-slate-950/80 px-2 py-0.5 font-mono text-[11px] text-slate-300"
          >
            #{record.id.slice(0, 8)}
          </span>
          <time dateTime={record.created_at} className="text-xs text-slate-400">
            {formatPromptTimestamp(record.created_at)}
          </time>
        </div>
      </div>

      <div className="mt-3.5 rounded-xl border border-slate-800/90 bg-slate-950/75 p-4">
        <p className="line-clamp-3 text-sm leading-relaxed whitespace-pre-line break-words text-slate-100">
          {record.raw_prompt}
        </p>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <span className="font-mono text-xs text-slate-400">
          {record.raw_prompt.length.toLocaleString('tr-TR')} karakter
        </span>

        <Link
          to={ROUTES.promptRequestDetail(record.id)}
          state={{ fromHistoryPath: resolvedHistoryPath }}
          className="inline-flex items-center gap-1.5 rounded-xl border border-indigo-500/40 bg-indigo-500/15 px-3.5 py-2 text-xs font-semibold text-indigo-200 transition-colors hover:border-indigo-400/60 hover:bg-indigo-500/25 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        >
          <span>Detayları Görüntüle</span>
          <span aria-hidden="true">→</span>
        </Link>
      </div>
    </article>
  )
}
