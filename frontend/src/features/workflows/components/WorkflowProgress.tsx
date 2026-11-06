import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ErrorAlert } from '../../../components/common/ErrorAlert'
import { isTerminal, trustedSelection } from '../contracts'
import { WorkflowClientError } from '../errors'
import { workflowMetadataQuery, workflowMonitoringQuery } from '../queries'
import { progressExplanation, trustworthyProgress, workflowStatusLabels } from '../progress'
import { useProgressVisibility } from '../useProgressVisibility'

const control = 'inline-flex min-h-11 items-center justify-center rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-50'
const operationNames = { create: 'Architect · Hazırlama', revise: 'Architect · İyileştirme', evaluate: 'Critic · Değerlendirme' } as const
const operationStates = { PENDING: 'Kayıt bekliyor', RUNNING: 'Tamamlanması henüz doğrulanmadı', COMPLETED: 'Tamamlandı', FAILED: 'Başarısız' } as const

export function WorkflowProgress({ workflowId, parentId }: { readonly workflowId: string; readonly parentId: string }) {
  const { ref, visible } = useProgressVisibility()
  const [paused, setPaused] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const headingId = useId()
  const history = useQuery({ ...workflowMetadataQuery(workflowId, parentId),
    enabled: visible && historyOpen, refetchOnMount: false, refetchOnReconnect: false, networkMode: 'always', staleTime: 2000 })
  const historyBroken = Boolean(history.data && (!trustworthyProgress(history.data.workflow) ||
    (isTerminal(history.data.workflow.status) && history.data.operations.some(v => v.incomplete || v.unresolved))))
  const query = useQuery(workflowMonitoringQuery(workflowId, parentId, visible && !paused && !historyBroken))
  const row = query.data
  const terminal = row && isTerminal(row.status)
  const historyRelevant = history.data && row && history.data.workflow.state_revision >= row.state_revision
  const uncertainTerminal = historyRelevant && terminal && history.data!.operations.some(v => v.incomplete || v.unresolved)
  const trusted = row && trustworthyProgress(row) && !historyBroken && !uncertainTerminal
  const selected = row && trusted ? trustedSelection(row) : null
  const error = query.error instanceof WorkflowClientError ? query.error : query.error ? new WorkflowClientError('network') : null
  const cancelled = error?.kind === 'cancelled'

  return (
    <section ref={ref} aria-labelledby={headingId} className="min-w-0 space-y-5 rounded-xl border border-slate-700 bg-slate-950/60 p-4 sm:p-5">
      <h3 id={headingId} className="text-base font-semibold text-slate-100">Kalıcı Workflow Durumu</h3>
      <dl className="grid min-w-0 gap-3 text-xs text-slate-400 sm:grid-cols-2">
        <div><dt>İzlenen workflow</dt><dd className="mt-1 break-all font-mono text-sm text-slate-100">{workflowId}</dd></div>
        <div><dt>Bağlı prompt isteği</dt><dd className="mt-1 break-all font-mono text-sm text-slate-100">{parentId}</dd></div>
      </dl>
      {row ? <>
        <div role="status" aria-live="polite" aria-atomic="true" className={`space-y-3 rounded-xl border p-4 ${!trusted || row.status === 'CLARIFICATION_REQUIRED' ? 'border-amber-500/40 bg-amber-950/20 text-amber-100' : row.status === 'FAILED' ? 'border-rose-500/40 bg-rose-950/30 text-rose-100' : terminal ? 'border-indigo-400/40 bg-indigo-950/30 text-indigo-100' : row.status === 'PENDING' ? 'border-slate-700 text-slate-200' : 'border-sky-500/40 bg-sky-950/20 text-sky-100'}`}>
          <p className="font-semibold">{trusted ? `Son okunan kayıtlı durum: ${workflowStatusLabels[row.status]}.` : 'Kaydın bütünlüğü veya önceki işlem sonucu incelenmeli.'}</p>
          <p className="text-sm leading-relaxed">{historyBroken || uncertainTerminal ? 'İşlem özetinin bütünlüğü veya tamamlanması doğrulanamıyor. Güvenilir tamamlanma veya yeni yürütme yetkisi çıkarılamaz. Otomatik izleme durdu; işlem özetini GET ile yenileyebilirsin.' : progressExplanation(row)}</p>
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <div><dt className="text-xs opacity-80">Kayıtlı değerlendirme turu</dt><dd>{row.current_round === 0 ? 'Henüz başlamadı' : row.current_round}</dd></div>
            <div><dt className="text-xs opacity-80">Doğrulanmış tamamlanan değerlendirme</dt><dd aria-label="Doğrulanmış tamamlanan değerlendirme sayısı">{trusted ? row.completed_rounds : 'Doğrulanamıyor'}</dd></div>
            <div><dt className="text-xs opacity-80">En fazla değerlendirme turu</dt><dd>{row.max_evaluation_rounds}</dd></div>
          </dl>
        </div>
        <p className="text-sm text-slate-300">Son doğrulanmış değerlendirme puanı: {selected?.total_score ?? 'Bilinmiyor'}.</p>
        {selected ? <p className="text-xs text-slate-400">Son değerlendirme: {selected.quality_status === 'qualified' ? 'Kalite koşulları sağlandı' : selected.quality_status === 'not_qualified' ? 'Kalite koşulları sağlanmadı' : 'Kalite değerlendirilemiyor'}. Yürütme durumu ayrı izlenir.</p> : null}
        <p className="text-xs text-slate-400">Son başarılı okuma (tarayıcı): <time dateTime={new Date(query.dataUpdatedAt).toISOString()}>{new Date(query.dataUpdatedAt).toLocaleTimeString('tr-TR')}</time>. Bu zaman sunucunun son güncelleme zamanı değildir.</p>
        {row.finished_at ? <p className="text-xs text-slate-400">Kayıtlı bitiş: <time dateTime={row.finished_at}>{new Date(row.finished_at).toLocaleString('tr-TR')}</time></p> : null}
      </> : <p role="status" className="min-h-12 text-sm text-slate-400">{query.isFetching ? 'Workflow durumu okunuyor...' : 'İlerleme görünür olduğunda durum okunacak.'}</p>}
      {error && !cancelled ? <ErrorAlert title="İlerleme yenilenemedi"
        message={`${error.message} Otomatik yenileme durdu. Bu hata workflow'un FAILED olduğu anlamına gelmez; varsa son alınan bilgiler gösteriliyor. Şimdi Yenile yalnızca GET gönderir.`}
        requestId={error.requestId} /> : null}
      {!error && row && !terminal && trusted ? <p className="text-xs text-slate-400">{paused ? 'İzleme duraklatıldı; sunucudaki işlem iptal edilmedi.' : visible ? 'Görünürken yaklaşık 2 saniyede bir okunur; gizli sekmede veya ekran dışında duraklar.' : 'Sekme veya ilerleme alanı görünür değil; otomatik okuma duraklatıldı.'}</p> : null}
      <div className="flex flex-wrap gap-3">
        <button type="button" className={control} disabled={query.isFetching || !visible}
          onClick={() => { void query.refetch({ cancelRefetch: false }) }}>Şimdi Yenile</button>
        {!terminal && trusted ? <button type="button" className={control} onClick={() => setPaused(!paused)}>{paused ? 'İzlemeyi Sürdür' : 'İzlemeyi Duraklat'}</button> : null}
        <button type="button" className={control} aria-expanded={historyOpen} onClick={() => setHistoryOpen(!historyOpen)}>{historyOpen ? 'İşlem Özetini Gizle' : 'İşlem Özetini Görüntüle'}</button>
        <span aria-hidden="true" className="min-h-4 min-w-20 self-center text-xs text-slate-400">{query.isFetching ? 'Okunuyor...' : '\u00a0'}</span>
      </div>
      {historyOpen ? <div className="space-y-3 border-t border-slate-800 pt-4">
        <p className="text-xs text-slate-400">İşlem özeti tek bir metadata okumasıdır; içerik, kanıt metni ve sağlayıcı etkinliği gösterilmez.</p>
        {history.isError ? <p role="alert" className="text-sm text-amber-200">İşlem özeti okunamadı. İlerleme durumu bundan ayrı izlenir.</p> : null}
        {history.data ? <>
          {!history.data.workflow.history_valid || history.data.workflow.records_truncated || history.data.workflow.findings.length ? <p className="text-sm text-amber-200">Geçmiş bütünlüğü doğrulanamıyor; bu kayıtlar tamamlanma kanıtı değildir.</p> : null}
          {history.data.operations.length === 0 ? <p className="text-sm text-slate-300">Henüz kayıtlı ajan işlemi yok.</p> : <ul className="space-y-2">{history.data.operations.map(value => <li key={value.id} className="rounded-lg border border-slate-800 p-3 text-sm text-slate-300">
            {operationNames[value.operation]} · {operationStates[value.status]}{value.evaluation_round === null ? '' : ` · Tur ${value.evaluation_round}`}
            {(value.incomplete || value.unresolved) ? <p className="mt-1 text-xs text-amber-200">Bu kaydın tamamlanması doğrulanmadı. Canlı sağlayıcı çağrısı veya yeniden başlatma izni anlamına gelmez.</p> : null}
            <p className="mt-1 text-xs text-slate-400">Kayıtlı başlangıç: <time dateTime={value.started_at}>{new Date(value.started_at).toLocaleString('tr-TR')}</time></p>
          </li>)}</ul>}
        </> : history.isFetching ? <p role="status" className="text-sm text-slate-400">İşlem özeti okunuyor...</p> : null}
        <button type="button" className={control} disabled={history.isFetching || !visible} onClick={() => { void history.refetch({ cancelRefetch: false }) }}>İşlem Özetini Yenile</button>
      </div> : null}
      {terminal ? <p className="text-sm text-slate-400">Ayrıntılı sonuç ve karşılaştırma görünümü bu ekranda henüz kullanılamıyor.</p> : null}
    </section>
  )
}
