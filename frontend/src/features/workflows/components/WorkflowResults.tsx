import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { hashKey, useQuery, useQueryClient } from '@tanstack/react-query'
import { promptRequestKeys } from '../../prompt-requests/hooks'
import type { PromptRequestRecord } from '../../prompt-requests/types'
import { boundQuestions, checkedSource, clarificationContentConflicts, sameQuestions, sameSource, type FollowupSource } from '../clarificationFollowup'
import type { WorkflowContentHistory, WorkflowMetadataHistory, WorkflowProgress, WorkflowResult } from '../contracts'
import { isTerminal } from '../contracts'
import { WorkflowClientError } from '../errors'
import { workflowKeys, workflowMetadataQuery, workflowResultQuery } from '../queries'
import { trustworthyProgress, workflowStatusLabels } from '../progress'
import { reconcileResult, trustworthyMetadata, versionForEvaluation } from '../readValidation'
import { blockClarificationFollowup, confirmResultIntegrity, isWorkflowIntegrityError, readResultIntegrity, revokeResultIntegrity, useResultIntegrity } from '../resultIntegrity'
import { useFollowupRoute } from '../useFollowupRoute'
import { CopyPromptButton } from './CopyPromptButton'
import { ClarificationFollowup } from './ClarificationFollowup'
import { EvaluationPanel } from './EvaluationPanel'
import { qualityLabels } from '../evaluationLabels'
import { RendererIdentity } from './RendererIdentity'
import { WorkflowHistoryContent } from './WorkflowHistoryContent'
import { WorkflowReadError } from './WorkflowReadError'
import { promptText, readButton } from './resultStyles'

const invalidContentRead = (error: unknown) => error instanceof WorkflowClientError &&
  (error.kind === 'malformed_response' || isWorkflowIntegrityError(error))

export function WorkflowResults({ workflowId, parentId, progress, unverified, source, progressChecking, defaultSelected = false }: {
  readonly workflowId: string; readonly parentId: string; readonly progress: WorkflowProgress | undefined
  readonly unverified: boolean
  readonly source?: FollowupSource
  readonly progressChecking?: boolean
  readonly defaultSelected?: boolean
}) {
  const id = useId()
  const { isCurrent: routeIsCurrent, entryKey } = useFollowupRoute(parentId, workflowId, defaultSelected)
  const client = useQueryClient()
  const [contentOpen, setContentOpen] = useState(false)
  const integrity = useResultIntegrity(workflowId, parentId)
  const integrityRevoked = integrity.revoked
  const reportIntegrityFailure = useCallback(() => revokeResultIntegrity(client, workflowId, parentId), [client, workflowId, parentId])
  const blockFollowup = useCallback(() => blockClarificationFollowup(client, workflowId, parentId), [client, workflowId, parentId])
  const metadata = useQuery({ ...workflowMetadataQuery(workflowId, parentId), staleTime: Infinity,
    refetchOnMount: false, refetchOnReconnect: false, networkMode: 'always' })
  const metadataBlocked = unverified || Boolean(progress && !trustworthyProgress(progress)) || metadata.isError ||
    Boolean(metadata.data && !trustworthyMetadata(metadata.data))
  const blocked = metadataBlocked || integrityRevoked
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
  const verifiedResult = !blocked && !result.isError && !result.isFetching && result.data && progress &&
    result.data.workflow.state_revision >= progress.state_revision && result.data.workflow.status === progress.status
    ? result.data : undefined
  const summary = metadata.data
  const reconciliation = result.data && summary && trustworthyMetadata(summary) ? reconcileResult(result.data, summary) : undefined
  useLayoutEffect(() => {
    if (reconciliation === 'conflict') reportIntegrityFailure()
  }, [reconciliation, reportIntegrityFailure])
  const integrityError = isWorkflowIntegrityError(result.error)
  const displayBlocked = blocked || reconciliation === 'conflict' || integrityError
  const awaitingReconciliation = reconciliation === 'newer'
  const contentBlocked = displayBlocked || awaitingReconciliation
  const historyVerified = !contentBlocked && !result.isError
  const data = contentBlocked ? undefined : verifiedResult
  const best = data?.workflow.status === 'COMPLETED' ? data.candidate : null
  const selected = data?.workflow.selected
  const contentHash = hashKey(workflowKeys.content(workflowId, parentId))
  const subscribeContent = useCallback((notify: () => void) => client.getQueryCache().subscribe(event => {
    if ((event.type === 'updated' || event.type === 'removed') && event.query.queryHash === contentHash) notify()
  }), [client, contentHash])
  const readContent = useCallback(() => client.getQueryState<WorkflowContentHistory>(workflowKeys.content(workflowId, parentId)),
    [client, workflowId, parentId])
  const content = useSyncExternalStore(subscribeContent, readContent, readContent)
  const original = result.data?.workflow.status === 'CLARIFICATION_REQUIRED' ? checkedSource(source, parentId) : undefined
  const clarificationConflict = Boolean(original && (invalidContentRead(content?.error) ||
    (content?.data && clarificationContentConflicts(original, result.data, content.data))))
  useLayoutEffect(() => {
    if (clarificationConflict && !integrity.clarificationBlocked) blockFollowup()
  }, [clarificationConflict, integrity.clarificationBlocked, blockFollowup])
  // Keep the editor mounted during revalidation of the same snapshot, with both
  // actions paused. Confirmed contradictions withdraw it entirely.
  const floor = integrity.observedRevision ?? 0
  const clarificationStale = Boolean(original && result.data && summary &&
    Math.min(result.data.workflow.state_revision, summary.workflow.state_revision) < floor)
  const questions = !contentBlocked && !clarificationConflict && !integrity.clarificationBlocked && !result.isError && !clarificationStale
    ? boundQuestions(workflowId, parentId, original, progress, result.data, summary, content?.data) : undefined
  const checking = Boolean(source?.checking || progressChecking || result.isFetching || metadata.isFetching ||
    content?.fetchStatus === 'fetching')

  function canSaveFollowup() {
    const currentIntegrity = readResultIntegrity(client, workflowId, parentId)
    if (!routeIsCurrent() || !source || !original || !questions || currentIntegrity.revoked || currentIntegrity.clarificationBlocked) return false
    const saved = client.getQueryState<PromptRequestRecord>(promptRequestKeys.detail(source.queryId))
    const run = client.getQueryState<WorkflowProgress>(workflowKeys.progress(workflowId, parentId))
    const latestResult = client.getQueryState<WorkflowResult>(workflowKeys.result(workflowId, parentId))
    const latestSummary = client.getQueryState<WorkflowMetadataHistory>(workflowKeys.metadata(workflowId, parentId))
    const latestContent = readContent()
    if ([saved, run, latestResult, latestSummary].some(read => read?.status !== 'success' || read.fetchStatus !== 'idle') ||
      (latestContent && (latestContent.fetchStatus !== 'idle' || invalidContentRead(latestContent.error)))) return false
    const latestSource = saved?.data && checkedSource({ record: saved.data, queryId: source.queryId }, parentId)
    if (!latestSource || !sameSource(original, latestSource) ||
      latestResult?.data?.workflow.state_revision !== result.data?.workflow.state_revision ||
      Math.min(latestResult?.data?.workflow.state_revision ?? -1, latestSummary?.data?.workflow.state_revision ?? -1) <
        (currentIntegrity.observedRevision ?? 0)) return false
    const latestQuestions = boundQuestions(workflowId, parentId, latestSource, run?.data, latestResult?.data, latestSummary?.data, latestContent?.data)
    return Boolean(latestQuestions && sameQuestions(questions, latestQuestions))
  }

  async function refreshResult() {
    const key = workflowKeys.result(workflowId, parentId)
    const observed = readResultIntegrity(client, workflowId, parentId)
    const before = client.getQueryState<WorkflowResult>(key)
    const read = await result.refetch({ cancelRefetch: false })
    const latestHistory = client.getQueryState<WorkflowMetadataHistory>(workflowKeys.metadata(workflowId, parentId))
    const latestProgress = client.getQueryState<WorkflowProgress>(workflowKeys.progress(workflowId, parentId))
    // A cancelled Query refetch can resolve with reverted cached data. Only a
    // newly committed successful read, even within the same millisecond, is proof.
    if (observed.revoked && !read.isError && read.data &&
      latestHistory?.status === 'success' && latestHistory.fetchStatus === 'idle' &&
      latestHistory.data && trustworthyMetadata(latestHistory.data) &&
      reconcileResult(read.data, latestHistory.data) === 'current' &&
      latestProgress?.status === 'success' && latestProgress.data && trustworthyProgress(latestProgress.data) &&
      read.data.workflow.state_revision >= latestProgress.data.state_revision &&
      read.data.workflow.state_revision >= (before?.data?.workflow.state_revision ?? 0) &&
      read.data.workflow.status === latestProgress.data.status &&
      (client.getQueryState(key)?.dataUpdateCount ?? 0) > (before?.dataUpdateCount ?? 0) &&
      readResultIntegrity(client, workflowId, parentId).revision === observed.revision &&
      readResultIntegrity(client, workflowId, parentId).clarificationBlocked === observed.clarificationBlocked) {
      // A previous rejected content snapshot is neither new evidence nor a fresh
      // history request. Close it and evict it before restoring result approval.
      setContentOpen(false)
      client.removeQueries({ queryKey: workflowKeys.content(workflowId, parentId), exact: true })
      confirmResultIntegrity(client, workflowId, parentId, observed)
    }
  }

  async function refreshClarification() {
    const history = await refreshMetadata({ cancelRefetch: false })
    if (routeIsCurrent() && history.isSuccess) await refreshResult()
  }

  return <section id="workflow-results" aria-labelledby={`${id}-heading`} className="min-w-0 scroll-mt-24 space-y-5 border-t border-slate-700 pt-6">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 id={`${id}-heading`} className="text-lg font-semibold text-slate-100">Sonuç ve Değerlendirme Geçmişi</h3>
      <button type="button" className={readButton} disabled={!terminal || result.isFetching || metadataBlocked}
        onClick={() => { void refreshResult() }}>Sonucu Yenile</button>
    </div>
    <dl className="grid min-w-0 gap-3 text-xs text-slate-400 sm:grid-cols-2">
      <div><dt>Sonucun workflow kimliği</dt><dd className="mt-1 break-all font-mono">{workflowId}</dd></div>
      <div><dt>Sonucun prompt isteği</dt><dd className="mt-1 break-all font-mono">{parentId}</dd></div>
    </dl>
    <p className="text-sm text-slate-300">Kayıtlı yürütme: {progress ? workflowStatusLabels[progress.status] : 'Henüz okunmadı'}.</p>
    {!terminal ? <p role="status" className="text-sm text-slate-400">Nihai sonuç henüz hazır değil. HTTP 202 veya devam eden bir tur, tamamlanmış sonuç değildir.</p> : null}
    {displayBlocked && !integrityError ? <p role="alert" className="rounded-xl border border-amber-500/40 bg-amber-950/20 p-4 text-sm text-amber-200">Sonucun bütünlüğü veya güncel okuması doğrulanamıyor. Prompt gösterimi ve kopyalama kapalı; kayıtlı metadata inceleme için korunur. Güvenli okumaları yenileyebilirsin.</p> : null}
    {awaitingReconciliation && !displayBlocked ? <p role="status" className="text-sm text-amber-200">Geçmiş özeti sonuçtan daha güncel. Sonucu Yenile ile güncel sonucu doğrula.</p> : null}
    {integrityRevoked ? <p className="text-sm text-amber-200">Sonuç veya geçmiş okuması bir bütünlük sorunu bildirdi. Sonucu Yenile ile yeniden doğrulanana kadar önceki prompt kullanılamaz.</p> : null}
    {clarificationStale && !displayBlocked ? <div className="space-y-2">
      <p role="status" className="text-sm text-amber-200">Daha güncel bir workflow kaydı görüldü. Taslak hazırlamak için açıklama kayıtlarını yenileyin.</p>
      <button type="button" className={readButton} disabled={checking} onClick={() => { void refreshClarification() }}>Açıklama Kayıtlarını Yenile</button>
    </div> : null}
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
      {best ? <section aria-label="Seçilen prompt" className="min-w-0 space-y-4">
        <div className="space-y-1"><h4 className="font-semibold text-slate-100">Seçilen En İyi Prompt</h4>
          <p className="text-xs text-slate-400">Sürüm {best.version.version_number} · Değerlendirme turu {best.version.evaluation_round ?? 'bilinmiyor'} · {best.version.id}</p>
          <p className="text-xs text-slate-400">Seçim backend tarafından yapıldı; orijinal metin prompt isteğinde ve karşılaştırma alanında ayrı gösterilir.</p>
        </div>
        <RendererIdentity version={best.version} />
        <p className="text-xs text-slate-400">Critic değerlendirmesi burada gösterilen metnin tam sürümü için geçerlidir. Farklı bir biçim ayrı değerlendirme gerektirir.</p>
        {best.version.target_mode === 'openai' && best.version.renderer_name === 'GenericPromptRenderer'
          ? <p className="text-xs text-slate-400">Bu kayıt OpenAI hedefiyle oluşturuldu ancak Generic renderer kullanılarak değerlendirilmiştir.</p> : null}
        <pre aria-label="Seçilen prompt metni" tabIndex={0} className={promptText}>{best.rendered_prompt}</pre>
        <CopyPromptButton key={best.version.id} text={best.rendered_prompt} disabled={!best.rendered_prompt.length || metadata.isFetching} />
        {data.evaluation && data.assessment ? <EvaluationPanel evaluation={data.evaluation} assessment={data.assessment} /> : null}
      </section> : data.workflow.status === 'COMPLETED' ? <p className="text-sm text-slate-400">API seçilmiş bir prompt sağlamadı. Kopyalanabilecek doğrulanmış metin yok.</p> : null}
      {data.workflow.status === 'COMPLETED' && data.workflow.completed_rounds < data.workflow.max_evaluation_rounds ? <p className="text-xs text-slate-400">Workflow yapılandırılan üst sınıra ulaşmadan sonlandı. Sonraki turların çalıştığı varsayılmaz.</p> : null}
    </> : null}
    {original && questions ? <div aria-label="Gerekli açıklamalar" role="region">
      <ClarificationFollowup key={`${parentId}:${workflowId}:${result.data?.workflow.state_revision}:${entryKey}`}
        source={original} questions={questions} checking={checking} canSave={canSaveFollowup} onBindingChange={blockFollowup} />
    </div> : data?.workflow.status === 'CLARIFICATION_REQUIRED' ? data.clarification_questions.length
      ? <section aria-label="Gerekli açıklamalar" className="space-y-2 text-sm text-slate-300">
        <h4 className="font-semibold">Kayıtlı Açıklama Soruları</h4>
        <ol className="list-inside list-decimal space-y-2 whitespace-pre-wrap [overflow-wrap:anywhere]">{data.clarification_questions.map((question, index) => <li key={index}>{question}</li>)}</ol>
        <p className="text-xs text-slate-400">Kayıtlar doğrulanamadığı için bu sorulardan yeni istek taslağı hazırlanamaz.</p>
      </section> : <p className="text-sm text-slate-400">API açıklama sorusu içeriği sağlamadı.</p> : null}
    <section aria-label="Değerlendirme metadata geçmişi" className="min-w-0 space-y-4 border-t border-slate-800 pt-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><h4 className="font-semibold text-slate-100">Değerlendirme Geçmişi</h4>
        <button type="button" className={readButton} disabled={metadata.isFetching} onClick={() => { void metadata.refetch({ cancelRefetch: false }) }}>Geçmiş Özetini Yenile</button></div>
      <p className="text-xs text-slate-400">Bu ilk görünüm yalnızca metadata içerir. Ayrıntılı promptlar ve Critic metinleri ayrı isteğinle yüklenir. Özet, son okunduğu andaki kayıttır.</p>
      <WorkflowReadError title="Geçmiş özeti okunamadı" error={metadata.error} />
      {metadata.isFetching ? <p role="status" className="text-sm text-slate-400">Geçmiş özeti okunuyor...</p> : null}
      {summary ? <>
        {!historyVerified ? <p className="text-sm text-amber-200">Bu geçmiş doğrulanmış tamamlanma veya kalite kanıtı değildir. Aşağıdaki bilgiler inceleme amaçlı kayıt özetleridir.</p> : null}
        {summary.evaluations.length === 0 ? <p className="text-sm text-slate-400">Henüz kayıtlı tamamlanmış değerlendirme yok.</p> : <ol className="space-y-3">{summary.evaluations.map(evaluation => {
          const version = historyVerified ? versionForEvaluation(summary, evaluation) : undefined
          return <li key={evaluation.id} className="min-w-0 space-y-2 rounded-xl border border-slate-700 p-4 text-sm text-slate-300">
            <p className="font-medium">Tur {evaluation.evaluation_round} · Kayıtlı değerlendirme{!version ? ' (doğrulanmadı)' : ''}</p>
            <p className="break-all font-mono text-xs">Prompt sürümü: {evaluation.prompt_version_id}</p>
            {version ? <RendererIdentity version={version} /> : <p className="text-xs text-slate-400">Bu tur için renderer kimliği doğrulanamadı.</p>}
            <p>Kayıtlı puan: {evaluation.stored_total_score ?? 'Değerlendirilemedi'} · {evaluation.quality_status ? qualityLabels[evaluation.quality_status] : 'Kalite sonucu bilinmiyor'}</p>
            <p className="text-xs text-slate-400">Kayıtlı tamamlanma: <time dateTime={evaluation.completed_at}>{new Date(evaluation.completed_at).toLocaleString('tr-TR')}</time></p>
          </li>
        })}</ol>}
        {summary.operations.filter(v => v.incomplete || v.unresolved || v.staged_unevaluated_candidate).length ? <p className="text-sm text-amber-200">Geçmişte tamamlanması doğrulanmamış veya henüz değerlendirilmemiş işlem kayıtları var. Bunlar tamamlanmış değerlendirme turu sayılmaz.</p> : null}
      </> : null}
      <button type="button" className={readButton} disabled={contentBlocked || !summary} aria-expanded={contentOpen} onClick={() => setContentOpen(!contentOpen)}>{contentOpen ? 'Geçmiş İçeriğini Kapat' : 'Prompt Sürümlerini ve Değerlendirmeleri Yükle'}</button>
      {contentOpen && !contentBlocked ? <WorkflowHistoryContent workflowId={workflowId} parentId={parentId} selectedVersionId={selected?.prompt_version_id}
        summary={summary} result={result.data} onIntegrityFailure={reportIntegrityFailure} /> : null}
    </section>
  </section>
}
