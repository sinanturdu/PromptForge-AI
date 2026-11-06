import { useLayoutEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { WorkflowClientError } from '../errors'
import { workflowContentQuery } from '../queries'
import { trustworthyMetadata } from '../readValidation'
import { EvaluationPanel } from './EvaluationPanel'
import { PromptComparison } from './PromptComparison'
import { readButton } from './resultStyles'
import { WorkflowReadError } from './WorkflowReadError'

export function WorkflowHistoryContent({ workflowId, parentId, selectedVersionId, onIntegrityFailure }: {
  readonly workflowId: string; readonly parentId: string; readonly selectedVersionId?: string | null
  readonly onIntegrityFailure: () => void
}) {
  const query = useQuery(workflowContentQuery(workflowId, parentId))
  const content = !query.isError && !query.isFetching ? query.data : undefined
  const trusted = content && trustworthyMetadata(content.metadata)
  const integrityFailed = Boolean(query.data && !trustworthyMetadata(query.data.metadata)) ||
    (query.error instanceof WorkflowClientError && query.error.errorCode === 'workflow_integrity_inconsistent')
  useLayoutEffect(() => {
    // Before paint, revoke older approval in the shared workflow-scoped record.
    // Neither this content panel nor its parent owns the revocation lifetime.
    if (integrityFailed) onIntegrityFailure()
  }, [integrityFailed, onIntegrityFailure])
  return <div className="min-w-0 space-y-5 border-t border-slate-700 pt-5">
    <WorkflowReadError title="Geçmiş içeriği okunamadı" error={query.error} />
    {query.isFetching ? <p role="status" className="text-sm text-slate-400">Prompt sürümleri ve değerlendirmeler okunuyor...</p> : null}
    {content && !trusted ? <p role="alert" className="text-sm text-amber-200">Geçmiş içeriğinin bütünlüğü doğrulanamıyor. Bu metinler karşılaştırma veya güvenilir sonuç olarak sunulmuyor.</p> : null}
    {trusted ? <>
      <PromptComparison key={query.dataUpdatedAt} content={content} selectedVersionId={selectedVersionId} />
      <section aria-label="Geçmiş Critic değerlendirmeleri" className="min-w-0 space-y-3">
        <h4 className="font-semibold text-slate-100">Kayıtlı Değerlendirmelerin Ayrıntıları</h4>
        {content.evaluations.length === 0 ? <p className="text-sm text-slate-400">İçeriği sağlanan tamamlanmış değerlendirme yok.</p> : content.evaluations.map(value => {
          const evaluation = content.metadata.evaluations.find(v => v.id === value.evaluation_id)!
          return <details key={value.evaluation_id} className="min-w-0 rounded-xl border border-slate-700 p-4">
            <summary className="cursor-pointer rounded text-sm font-medium text-slate-200 focus-visible:outline-2 focus-visible:outline-indigo-400">Tur {evaluation.evaluation_round} · Değerlendirmeyi İncele</summary>
            <div className="min-w-0 pt-4"><EvaluationPanel evaluation={evaluation} assessment={value.assessment} /></div>
          </details>
        })}
      </section>
    </> : null}
    <button type="button" className={readButton} disabled={query.isFetching} onClick={() => { void query.refetch({ cancelRefetch: false }) }}>Geçmiş İçeriğini Yenile</button>
  </div>
}
