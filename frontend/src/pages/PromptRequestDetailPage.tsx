import { Link, useLocation, useParams } from 'react-router'
import { ROUTES } from '../app/routes'
import { ErrorAlert } from '../components/common/ErrorAlert'
import { OptimizationPanel } from '../features/workflows/components/OptimizationPanel'
import { sameId } from '../features/workflows/readValidation'
import { formatPromptTimestamp } from '../features/prompt-requests/formatters'
import { usePromptRequestQuery } from '../features/prompt-requests/hooks'
import {
  formatOutputLanguage,
  getPromptDetailMeta,
  getReasoningPreferenceMeta,
  getResponseDetailMeta,
  getTargetFamilyMeta,
} from '../features/prompt-requests/types'

interface NavigationLocationState {
  readonly justCreated?: boolean
  readonly fromHistoryPath?: string
}

export function PromptRequestDetailPage() {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const navState = location.state as NavigationLocationState | null
  const justCreated = Boolean(navState?.justCreated)
  const historyReturnPath =
    typeof navState?.fromHistoryPath === 'string' &&
    (navState.fromHistoryPath === ROUTES.promptHistory ||
      navState.fromHistoryPath.startsWith(`${ROUTES.promptHistory}?`))
      ? navState.fromHistoryPath
      : ROUTES.promptHistory

  const { data: record, error, isLoading, isFetching, refetch } = usePromptRequestQuery(id)

  if (isLoading) {
    return (
      <section
        role="status"
        aria-live="polite"
        className="space-y-4 rounded-2xl border border-slate-800/90 bg-slate-900/50 p-6 sm:p-8"
      >
        <div className="h-5 w-48 animate-pulse rounded bg-slate-800" />
        <p className="text-sm text-slate-300">Kaydedilen prompt isteği yükleniyor...</p>
        <div className="space-y-3 pt-2">
          <div className="h-24 w-full animate-pulse rounded-xl bg-slate-950/70" />
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="h-16 animate-pulse rounded-xl bg-slate-950/70" />
            <div className="h-16 animate-pulse rounded-xl bg-slate-950/70" />
            <div className="h-16 animate-pulse rounded-xl bg-slate-950/70" />
          </div>
        </div>
      </section>
    )
  }

  if (error || !record) {
    return (
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-3">
          <Link
            to={historyReturnPath}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/80 px-4 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-slate-700 hover:bg-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            ← Prompt Geçmişine Dön
          </Link>
          <Link
            to={ROUTES.newPrompt}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/80 px-4 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-slate-700 hover:bg-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            + Yeni Prompt İsteği Oluştur
          </Link>
        </div>

        <ErrorAlert
          title="Prompt isteği görüntülenemedi"
          message={
            error?.userMessage ?? 'İstenen kayıt yüklenirken beklenmeyen bir sorun oluştu.'
          }
          validationIssues={error?.validationIssues}
          requestId={error?.requestId}
          actions={
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => {
                  void refetch()
                }}
                className="rounded-lg border border-rose-400/40 bg-rose-950/60 px-3.5 py-1.5 text-xs font-medium text-rose-100 transition-colors hover:bg-rose-900/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
              >
                Tekrar Dene
              </button>
              <Link
                to={ROUTES.newPrompt}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3.5 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
              >
                Yeni Prompt Sayfasına Dön
              </Link>
            </div>
          }
        />
      </div>
    )
  }

  const targetMeta = getTargetFamilyMeta(record.target_mode)
  const promptDetailMeta = getPromptDetailMeta(record.prompt_detail_level)
  const responseDetailMeta = getResponseDetailMeta(record.response_detail_preference)
  const reasoningMeta = getReasoningPreferenceMeta(record.reasoning_preference)

  return (
    <div className="space-y-6">
      {/* Top Action & Breadcrumb Bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-indigo-400">
            Kaydedilen Prompt İsteği
          </p>
          <h1 className="text-2xl font-bold tracking-tight text-slate-50 sm:text-3xl">
            Prompt İsteği Detayları
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Link
            to={historyReturnPath}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-900/90 px-4 py-2.5 text-xs font-semibold text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            ← Prompt Geçmişine Dön
          </Link>

          <Link
            to={ROUTES.newPrompt}
            className="inline-flex items-center rounded-xl bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            + Yeni Prompt İsteği Oluştur
          </Link>
        </div>
      </div>

      {/* Optional Post-Submit Confirmation Banner */}
      {justCreated ? (
        <div
          role="status"
          className="rounded-xl border border-emerald-500/40 bg-emerald-950/35 p-4 text-sm text-emerald-100"
        >
          <p className="font-semibold text-emerald-200">
            Prompt isteğiniz başarıyla kaydedildi.
          </p>
          <p className="mt-0.5 text-xs text-emerald-100/90">
            Bu bağlantıyla kaydını daha sonra yeniden görüntüleyebilirsin.
          </p>
        </div>
      ) : null}

      {/* Metadata Header Card: UUID & Created Timestamp */}
      <section
        aria-label="Kayıt üst verileri"
        className="grid gap-4 rounded-2xl border border-slate-800/90 bg-slate-900/50 p-5 sm:grid-cols-2"
      >
        <div className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
            İstek Kimliği (UUID)
          </span>
          <p className="break-all font-mono text-sm text-slate-100">{record.id}</p>
        </div>

        <div className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
            Oluşturulma Zamanı
          </span>
          <p className="text-sm text-slate-100">
            <time dateTime={record.created_at}>{formatPromptTimestamp(record.created_at)}</time>
          </p>
        </div>
      </section>

      {/* Main Content Grid */}
      <div className="grid gap-6 lg:grid-cols-12">
        {/* Raw Prompt & Optional Context */}
        <div className="min-w-0 space-y-6 lg:col-span-7">
          <section
            aria-labelledby="detail-raw-prompt-heading"
            className="space-y-3 rounded-2xl border border-slate-800/90 bg-slate-900/60 p-5 sm:p-6"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
              <h2 id="detail-raw-prompt-heading" className="text-sm font-semibold text-slate-100">
                Orijinal Ham Prompt
              </h2>
              <span className="font-mono text-xs text-slate-400">
                {record.raw_prompt.length.toLocaleString('tr-TR')} karakter
              </span>
            </div>
            <div className="rounded-xl border border-slate-800/90 bg-slate-950/80 p-4 text-sm leading-relaxed whitespace-pre-wrap break-words text-slate-100">
              {record.raw_prompt}
            </div>
          </section>

          <section
            aria-labelledby="detail-user-context-heading"
            className="space-y-3 rounded-2xl border border-slate-800/90 bg-slate-900/60 p-5 sm:p-6"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
              <h2
                id="detail-user-context-heading"
                className="text-sm font-semibold text-slate-100"
              >
                Proje ve Kullanım Bağlamı
              </h2>
              {record.user_context ? (
                <span className="font-mono text-xs text-slate-400">
                  {record.user_context.length.toLocaleString('tr-TR')} karakter
                </span>
              ) : null}
            </div>
            {record.user_context ? (
              <div className="rounded-xl border border-slate-800/90 bg-slate-950/80 p-4 text-sm leading-relaxed whitespace-pre-wrap break-words text-slate-200">
                {record.user_context}
              </div>
            ) : (
              <p className="rounded-xl border border-dashed border-slate-800 bg-slate-950/50 p-4 text-xs text-slate-400">
                Bu istek için ek proje bağlamı belirtilmedi.
              </p>
            )}
          </section>
        </div>

        {/* Saved Preferences & Honest AI Milestone Notice */}
        <div className="min-w-0 space-y-6 lg:col-span-5">
          <section
            aria-labelledby="detail-preferences-heading"
            className="space-y-4 rounded-2xl border border-slate-800/90 bg-slate-900/60 p-5 sm:p-6"
          >
            <h2
              id="detail-preferences-heading"
              className="border-b border-slate-800/80 pb-3 text-sm font-semibold text-slate-100"
            >
              Kaydedilen Tercihler
            </h2>

            <dl className="space-y-3.5 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800/80 bg-slate-950/70 px-3.5 py-2.5">
                <dt className="text-xs font-medium text-slate-400">Hedef Yapay Zekâ Ailesi</dt>
                <dd className="flex items-center gap-2 font-medium text-slate-100">
                  <span>{targetMeta.label}</span>
                  <span className="rounded border border-indigo-500/30 bg-indigo-500/10 px-1.5 py-0.5 font-mono text-[11px] text-indigo-300">
                    {record.target_mode}
                  </span>
                </dd>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800/80 bg-slate-950/70 px-3.5 py-2.5">
                <dt className="text-xs font-medium text-slate-400">Çıktı Dili</dt>
                <dd className="font-medium text-slate-100">
                  {formatOutputLanguage(record.output_language)}
                </dd>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800/80 bg-slate-950/70 px-3.5 py-2.5">
                <dt className="text-xs font-medium text-slate-400">Görev Profili</dt>
                <dd className="min-w-0 break-all font-mono text-xs text-slate-200">
                  {record.task_profile ?? 'Belirtilmedi'}
                </dd>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800/80 bg-slate-950/70 px-3.5 py-2.5">
                <dt className="text-xs font-medium text-slate-400">Prompt Detay Seviyesi</dt>
                <dd className="flex items-center gap-2 font-medium text-slate-100">
                  <span>{promptDetailMeta.label}</span>
                  <span className="font-mono text-xs text-slate-400">
                    ({record.prompt_detail_level})
                  </span>
                </dd>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800/80 bg-slate-950/70 px-3.5 py-2.5">
                <dt className="text-xs font-medium text-slate-400">Yanıt Detay Tercihi</dt>
                <dd className="flex items-center gap-2 font-medium text-slate-100">
                  <span>{responseDetailMeta.label}</span>
                  <span className="font-mono text-xs text-slate-400">
                    ({record.response_detail_preference})
                  </span>
                </dd>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800/80 bg-slate-950/70 px-3.5 py-2.5">
                <dt className="text-xs font-medium text-slate-400">Akıl Yürütme Tercihi</dt>
                <dd className="flex items-center gap-2 font-medium text-slate-100">
                  <span>{reasoningMeta.label}</span>
                  <span className="font-mono text-xs text-slate-400">
                    ({record.reasoning_preference})
                  </span>
                </dd>
              </div>
            </dl>
          </section>

        </div>
      </div>
      <OptimizationPanel key={record.id} promptRequestId={record.id}
        source={id && typeof record.id === 'string' && sameId(record.id, id) ? { record, queryId: id, checking: isFetching } : undefined} />
    </div>
  )
}
