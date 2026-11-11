import { useId, useLayoutEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ErrorAlert } from '../../../components/common/ErrorAlert'
import type { WorkflowCursor } from '../contracts'
import { WorkflowClientError } from '../errors'
import { workflowListQuery } from '../queries'
import { validWorkflowId, workflowStatusLabels } from '../progress'
import { WorkflowProgress } from './WorkflowProgress'

export function WorkflowMonitor({ parentId, defaultWorkflowId, followDefault }: {
  readonly parentId: string; readonly defaultWorkflowId?: string | null; readonly followDefault: boolean
}) {
  const [search, setSearch] = useSearchParams()
  const selected = search.has('workflow') ? search.get('workflow') : followDefault ? defaultWorkflowId : null
  const valid = validWorkflowId(parentId) && validWorkflowId(selected) && search.getAll('workflow').length <= 1
  const [closedId, setClosedId] = useState<string | null>(null)
  const [listOpen, setListOpen] = useState(false)
  const [cursors, setCursors] = useState<readonly WorkflowCursor[]>([])
  const id = useId()
  const section = useRef<HTMLElement>(null)
  useLayoutEffect(() => {
    // Selection/acceptance reveals the monitor once. Poll responses never move
    // focus or scroll; auto scrolling uses no animation (including reduced motion).
    if (valid) section.current?.scrollIntoView?.({ block: 'start', behavior: 'instant' })
  }, [selected, valid])
  const list = useQuery({ ...workflowListQuery(parentId, { limit: 20, before: cursors.at(-1) }),
    enabled: listOpen && validWorkflowId(parentId), refetchOnMount: false, refetchOnReconnect: false, networkMode: 'always' })
  const error = list.error instanceof WorkflowClientError ? list.error : null
  function select(workflowId: string) {
    const next = new URLSearchParams(search)
    next.set('workflow', workflowId)
    setClosedId(null)
    setSearch(next)
  }
  const control = 'min-h-11 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:opacity-50'

  return <section ref={section} id="workflow-monitor" tabIndex={-1} aria-labelledby={`${id}-heading`} className="min-w-0 scroll-mt-24 space-y-5 rounded-2xl border border-indigo-500/25 bg-slate-900/60 p-5 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 sm:p-6">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <h2 id={`${id}-heading`} className="text-lg font-semibold text-slate-100">Workflow İlerlemesi</h2>
      <button type="button" className={control} aria-expanded={listOpen} onClick={() => setListOpen(!listOpen)}>Kayıtlı Workflow’ları {listOpen ? 'Gizle' : 'Görüntüle'}</button>
    </header>
    <p className="text-xs leading-relaxed text-slate-400">Görüntüleme ve seçim yalnızca GET kullanır; workflow oluşturmaz, başlatmaz veya kurtarma yürütmez.</p>
    {listOpen ? <div className="space-y-3">
      <label htmlFor={`${id}-select`} className="block text-sm text-slate-200">Bu promptun workflow’ları</label>
      <select id={`${id}-select`} value={valid ? selected : ''} onChange={event => { if (validWorkflowId(event.target.value)) select(event.target.value) }}
        className="min-h-11 w-full min-w-0 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">
        <option value="">Workflow seç</option>
        {valid && !list.data?.items.some(row => row.id === selected) ? <option value={selected!}>Seçili workflow · {selected!.slice(0, 8)}</option> : null}
        {list.data?.items.map(row => <option key={row.id} value={row.id}>{row.id.slice(0, 8)} · {workflowStatusLabels[row.status]} · {new Date(row.created_at).toLocaleDateString('tr-TR')}</option>)}
      </select>
      {list.isFetching ? <p role="status" className="text-xs text-slate-400">Workflow listesi okunuyor...</p> : null}
      {list.data?.items.length === 0 ? <p className="text-sm text-slate-400">Bu sayfada kayıtlı workflow yok.</p> : null}
      {error ? <ErrorAlert title="Workflow listesi okunamadı" message={`${error.message} API kapalı veya salt okunur olabilir; bu yanıt yürütme hazır olduğunu göstermez.`} requestId={error.requestId} /> : null}
      <div className="flex flex-wrap gap-3">
        <button type="button" className={control} disabled={list.isFetching} onClick={() => { void list.refetch({ cancelRefetch: false }) }}>Listeyi Yenile</button>
        <button type="button" className={control} disabled={!cursors.length || list.isFetching} onClick={() => setCursors(cursors.slice(0, -1))}>Önceki Workflow Sayfası</button>
        <button type="button" className={control} disabled={!list.data?.next_cursor || list.isFetching || list.isError}
          onClick={() => { if (list.data?.next_cursor) setCursors([...cursors, list.data.next_cursor]) }}>Sonraki Workflow Sayfası</button>
      </div>
      <p className="text-xs text-slate-400">Sayfada en fazla 20 kayıt. Liste bütünlük denetimi veya yeniden başlatma yetkisi değildir.</p>
    </div> : null}
    {selected && !valid ? <ErrorAlert title="Geçersiz workflow kimliği" message="Bu bağlantı için ilerleme isteği gönderilmedi. Bu prompte bağlı kayıtlı workflow’lardan birini seç." />
      : valid ? <>
        <button type="button" className={control} aria-expanded={closedId !== selected} onClick={() => setClosedId(closedId === selected ? null : selected!)}>{closedId === selected ? 'İzlemeyi Aç' : 'İzlemeyi Kapat'}</button>
        {closedId !== selected ? <WorkflowProgress key={`${parentId}:${selected}`} parentId={parentId} workflowId={selected!} /> : <p className="text-sm text-slate-400">İzleme kapalı; otomatik GET gönderilmiyor. Sunucudaki işlem iptal edilmedi.</p>}
      </> : <p className="text-sm text-slate-400">Başlatılan workflow burada izlenir. Önceden oluşturulmuş bir kaydı seçerek de güvenle görüntüleyebilirsin.</p>}
  </section>
}
