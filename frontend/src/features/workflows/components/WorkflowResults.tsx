import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { WorkflowProgress } from '../contracts'
import { isTerminal } from '../contracts'
import { workflowMetadataQuery, workflowResultQuery } from '../queries'
import { trustworthyProgress, workflowStatusLabels } from '../progress'
import { trustworthyMetadata } from '../readValidation'
import { CopyPromptButton } from './CopyPromptButton'
import { EvaluationPanel } from './EvaluationPanel'
import { qualityLabels } from '../evaluationLabels'
import { WorkflowHistoryContent } from './WorkflowHistoryContent'
import { WorkflowReadError } from './WorkflowReadError'
import { promptText, readButton } from './resultStyles'

export function WorkflowResults({ workflowId, parentId, progress, unverified }: {
  readonly workflowId: string; readonly parentId: string; readonly progress: WorkflowProgress | undefined
  readonly unverified: boolean
}) {
  const id = useId()
  const [contentOpen, setContentOpen] = useState(false)
  const metadata = useQuery({ ...workflowMetadataQuery(workflowId, parentId), staleTime: Infinity,
    refetchOnMount: false, refetchOnReconnect: false, networkMode: 'always' })
  const blocked = unverified || Boolean(progress && !trustworthyProgress(progress)) || metadata.isError ||
    Boolean(metadata.data && !trustworthyMetadata(metadata.data))
  const terminal = progress && isTerminal(progress.status)
  const result = useQuery({ ...workflowResultQuery(workflowId, parentId),
    enabled: query => Boolean(terminal && metadata.data && !blocked && query.state.status !== 'error') })
  const reconciledRevision = useRef<number | null>(null)
  const { data: metadataSnapshot, refetch: refreshMetadata } = metadata
  useEffect(() => {
    const revision = result.data?.workflow.state_revision
    if (blocked || revision === undefined || reconciledRevision.current === revision) return
    reconciledRevision.current = revision
    // An open view may have read metadata while the workflow was still active.
    // Refresh that older snapshot once, not on a timer or on every render/error.
    if (metadataSnapshot && metadataSnapshot.workflow.state_revision < revision) {
      void refreshMetadata({ cancelRefetch: false })
    }
  }, [blocked, result.data, metadataSnapshot, refreshMetadata])
  const data = !blocked && !result.isError && !result.isFetching && result.data && progress &&
    result.data.workflow.state_revision >= progress.state_revision && result.data.workflow.status === progress.status
    ? result.data : undefined
  const best = data?.workflow.status === 'COMPLETED' ? data.candidate : null
  const selected = data?.workflow.selected
  const summary = metadata.data

  return <section id="workflow-results" aria-labelledby={`${id}-heading`} className="min-w-0 scroll-mt-24 space-y-5 border-t border-slate-700 pt-6">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 id={`${id}-heading`} className="text-lg font-semibold text-slate-100">Sonuç ve Değerlendirme Geçmişi</h3>
      <button type="button" className={readButton} disabled={!terminal || result.isFetching || blocked}
        onClick={() => { void result.refetch({ cancelRefetch: false }) }}>Sonucu Yenile</button>
    </div>
    <dl className="grid min-w-0 gap-3 text-xs text-slate-400 sm:grid-cols-2">
      <div><dt>Sonucun workflow kimliği</dt><dd className="mt-1 break-all font-mono">{workflowId}</dd></div>
      <div><dt>Sonucun prompt isteği</dt><dd className="mt-1 break-all font-mono">{parentId}</dd></div>
    </dl>
    <p className="text-sm text-slate-300">Kayıtlı yürütme: {progress ? workflowStatusLabels[progress.status] : 'Henüz okunmadı'}.</p>
    {!terminal ? <p role="status" className="text-sm text-slate-400">Nihai sonuç henüz hazır değil. HTTP 202 veya devam eden bir tur, tamamlanmış sonuç değildir.</p> : null}
    {blocked ? <p role="alert" className="rounded-xl border border-amber-500/40 bg-amber-950/20 p-4 text-sm text-amber-200">Sonucun bütünlüğü veya güncel okuması doğrulanamıyor. Prompt gösterimi ve kopyalama kapalı; kayıtlı metadata inceleme için korunur. Güvenli okumaları yenileyebilirsin.</p> : null}
    <WorkflowReadError title="Sonuç okunamadı" error={result.error} />
    {terminal && !blocked && (result.isFetching || !metadata.data) ? <p role="status" className="text-sm text-slate-400">Doğrulanmış sonuç okunuyor...</p> : null}
    {data ? <>
      <div className={`space-y-2 rounded-xl border p-4 ${data.workflow.status === 'COMPLETED' ? 'border-indigo-400/40 bg-indigo-950/20' : 'border-amber-500/40 bg-amber-950/20'}`}>
        <p className="font-semibold text-slate-100">{data.workflow.status === 'COMPLETED' ? 'Yürütme tamamlandı; sonuç bütünlüğü doğrulandı.' : data.workflow.status === 'FAILED' ? 'Yürütme başarısız; başarılı bir optimizasyon sonucu sunulmuyor.' : 'Ek açıklama gerekiyor; nihai prompt sunulmuyor.'}</p>
        <p className="text-sm text-slate-300">Kalite sonucu: {selected ? qualityLabels[selected.quality_status] : 'Değerlendirme mevcut değil'}.</p>
        {selected ? <p className="text-sm text-slate-300">Backend tarafından seçilen değerlendirmenin puanı: {selected.total_score ?? 'Değerlendirilemedi'}. Tamamlanma ile kalite koşulları ayrı değerlendirilir.</p> : null}
        {data.workflow.status === 'COMPLETED' && selected?.quality_status !== 'qualified' ? <p className="text-sm text-amber-200">Bu sonuç kalite koşullarını sağlamış olarak sunulmuyor. Bütünlüğü doğrulanan metni kullanmadan önce değerlendirmeyi incele.</p> : null}
        {data.workflow.status !== 'COMPLETED' ? <p className="text-sm text-slate-400">Yeni yürütme veya otomatik kurtarma yapılmaz. Varsa önceki adayları geçmiş içeriğinden inceleyebilirsin.</p> : null}
      </div>
      {data.workflow.status === 'CLARIFICATION_REQUIRED' ? data.clarification_questions.length ? <section aria-label="Gerekli açıklamalar" className="space-y-2 text-sm text-slate-300">
        <h4 className="font-semibold">Kayıtlı Açıklama Soruları</h4><ul className="list-inside list-disc space-y-2 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{data.clarification_questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
        <p className="text-xs text-slate-400">Bu ekranda yanıt düzenleme veya yeniden başlatma bulunmuyor.</p>
      </section> : <p className="text-sm text-slate-400">API açıklama sorusu içeriği sağlamadı.</p> : null}
      {best ? <section aria-label="Seçilen prompt" className="min-w-0 space-y-4">
        <div className="space-y-1"><h4 className="font-semibold text-slate-100">Seçilen En İyi Prompt</h4>
          <p className="text-xs text-slate-400">Sürüm {best.version.version_number} · Değerlendirme turu {best.version.evaluation_round ?? 'bilinmiyor'} · {best.version.id}</p>
          <p className="text-xs text-slate-400">Seçim backend tarafından yapıldı; orijinal metin prompt isteğinde ve karşılaştırma alanında ayrı gösterilir.</p>
        </div>
        <pre aria-label="Seçilen prompt metni" tabIndex={0} className={promptText}>{best.rendered_prompt}</pre>
        <CopyPromptButton key={best.version.id} text={best.rendered_prompt} disabled={!best.rendered_prompt.length || metadata.isFetching} />
        {data.evaluation && data.assessment ? <EvaluationPanel evaluation={data.evaluation} assessment={data.assessment} /> : null}
      </section> : data.workflow.status === 'COMPLETED' ? <p className="text-sm text-slate-400">API seçilmiş bir prompt sağlamadı. Kopyalanabilecek doğrulanmış metin yok.</p> : null}
      {data.workflow.status === 'COMPLETED' && data.workflow.completed_rounds < data.workflow.max_evaluation_rounds ? <p className="text-xs text-slate-400">Workflow yapılandırılan üst sınıra ulaşmadan sonlandı. Sonraki turların çalıştığı varsayılmaz.</p> : null}
    </> : null}
    <section aria-label="Değerlendirme metadata geçmişi" className="min-w-0 space-y-4 border-t border-slate-800 pt-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><h4 className="font-semibold text-slate-100">Değerlendirme Geçmişi</h4>
        <button type="button" className={readButton} disabled={metadata.isFetching} onClick={() => { void metadata.refetch({ cancelRefetch: false }) }}>Geçmiş Özetini Yenile</button></div>
      <p className="text-xs text-slate-400">Bu ilk görünüm yalnızca metadata içerir. Ayrıntılı promptlar ve Critic metinleri ayrı isteğinle yüklenir. Özet, son okunduğu andaki kayıttır.</p>
      <WorkflowReadError title="Geçmiş özeti okunamadı" error={metadata.error} />
      {metadata.isFetching ? <p role="status" className="text-sm text-slate-400">Geçmiş özeti okunuyor...</p> : null}
      {summary ? <>
        {!trustworthyMetadata(summary) ? <p className="text-sm text-amber-200">Bu geçmiş doğrulanmış tamamlanma veya kalite kanıtı değildir. Aşağıdaki bilgiler inceleme amaçlı kayıt özetleridir.</p> : null}
        {summary.evaluations.length === 0 ? <p className="text-sm text-slate-400">Henüz kayıtlı tamamlanmış değerlendirme yok.</p> : <ol className="space-y-3">{summary.evaluations.map(evaluation => <li key={evaluation.id} className="min-w-0 space-y-2 rounded-xl border border-slate-700 p-4 text-sm text-slate-300">
          <p className="font-medium">Tur {evaluation.evaluation_round} · Kayıtlı değerlendirme{!trustworthyMetadata(summary) ? ' (doğrulanmadı)' : ''}</p>
          <p className="break-all font-mono text-xs">Prompt sürümü: {evaluation.prompt_version_id}</p>
          <p>Kayıtlı puan: {evaluation.stored_total_score ?? 'Değerlendirilemedi'} · {evaluation.quality_status ? qualityLabels[evaluation.quality_status] : 'Kalite sonucu bilinmiyor'}</p>
          <p className="text-xs text-slate-400">Kayıtlı tamamlanma: <time dateTime={evaluation.completed_at}>{new Date(evaluation.completed_at).toLocaleString('tr-TR')}</time></p>
        </li>)}</ol>}
        {summary.operations.filter(v => v.incomplete || v.unresolved || v.staged_unevaluated_candidate).length ? <p className="text-sm text-amber-200">Geçmişte tamamlanması doğrulanmamış veya henüz değerlendirilmemiş işlem kayıtları var. Bunlar tamamlanmış değerlendirme turu sayılmaz.</p> : null}
      </> : null}
      <button type="button" className={readButton} disabled={blocked || !summary} aria-expanded={contentOpen} onClick={() => setContentOpen(!contentOpen)}>{contentOpen ? 'Geçmiş İçeriğini Kapat' : 'Prompt Sürümlerini ve Değerlendirmeleri Yükle'}</button>
      {contentOpen && !blocked ? <WorkflowHistoryContent workflowId={workflowId} parentId={parentId} selectedVersionId={selected?.prompt_version_id} /> : null}
    </section>
  </section>
}
