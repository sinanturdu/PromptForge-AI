import { useId, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ROUTES } from '../../../app/routes'
import { ErrorAlert } from '../../../components/common/ErrorAlert'
import { useOptimization } from '../useOptimization'
import { WorkflowMonitor } from './WorkflowMonitor'

const button = 'inline-flex min-h-11 items-center justify-center rounded-xl px-4 py-2.5 text-sm font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-50'
const primary = `${button} bg-indigo-600 text-white hover:bg-indigo-500`
const secondary = `${button} border border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800`

export function OptimizationPanel({ promptRequestId }: { readonly promptRequestId: string }) {
  const [search, setSearch] = useSearchParams()
  function monitor(workflowId: string) {
    const next = new URLSearchParams(search)
    next.set('workflow', workflowId)
    setSearch(next, { replace: true })
  }
  const ui = useOptimization(promptRequestId, monitor)
  const sectionId = useId()
  const roundsId = useId()
  const workflowId = ui.attempt?.workflowId
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); void ui.create() }

  return (<>
    <section aria-labelledby={`${sectionId}-heading`} className="min-w-0 space-y-5 rounded-2xl border border-indigo-500/25 bg-slate-900/60 p-5 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <h2 id={`${sectionId}-heading`} className="text-lg font-semibold text-slate-100">Prompt Optimizasyonu</h2>
          <p className="max-w-2xl text-sm leading-relaxed text-slate-300">
            Prompt kaydın hazır. Kaydetmek optimizasyon başlatmaz; önce bir workflow oluştur, sonra ayrıca başlat.
          </p>
          <p className="text-xs leading-relaxed text-slate-400">
            Bu özellik yalnızca doğrulanmış yerel uygulamada ve etkin backend ayarlarıyla kullanılabilir.
          </p>
        </div>
        <button type="button" aria-expanded={ui.expanded} aria-controls={sectionId}
          disabled={ui.busy} className={secondary} onClick={() => ui.setExpanded(!ui.expanded)}>
          {ui.expanded ? 'Yapılandırmayı Gizle' : 'Optimizasyonu Yapılandır'}
        </button>
      </header>

      {ui.expanded ? <div id={sectionId} className="min-w-0 space-y-5">
        <ol aria-label="Optimizasyon adımları" className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-300">
          <li>1. Prompt kaydedildi</li>
          <li aria-current={!workflowId ? 'step' : undefined}>2. Workflow oluştur</li>
          <li aria-current={workflowId ? 'step' : undefined}>3. Optimizasyonu başlat</li>
        </ol>
        {ui.storageBlocked ? <ErrorAlert title="Güvenli deneme kaydı kullanılamıyor"
          message="Tarayıcının oturum kaydı okunamadı veya korunamadı. Güvenlik için yeni oluşturma/başlatma isteği gönderilmiyor. Promptunu kaydetmeye ve görüntülemeye devam edebilirsin." /> : null}

        {!workflowId ? <form onSubmit={submit} noValidate className="space-y-4" aria-busy={ui.action === 'create'}>
          <div className="max-w-sm space-y-2">
            <label htmlFor={roundsId} className="block text-sm font-medium text-slate-200">En fazla değerlendirme turu</label>
            <select id={roundsId} value={ui.rounds} onChange={event => ui.setRounds(event.target.value)}
              disabled={ui.busy || Boolean(ui.attempt) || ui.storageBlocked}
              aria-invalid={Boolean(ui.roundError)} aria-describedby={`${roundsId}-help${ui.roundError ? ` ${roundsId}-error` : ''}`}
              className="min-h-11 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:opacity-60">
              <option value="1">1 tur</option><option value="2">2 tur</option><option value="3">3 tur (varsayılan)</option>
            </select>
            <p id={`${roundsId}-help`} className="text-xs leading-relaxed text-slate-400">En fazla 3 tur uygulanır; uygun sonuç daha erken elde edilirse workflow erken sonlanabilir.</p>
            {ui.roundError ? <p id={`${roundsId}-error`} role="alert" className="text-sm text-rose-300">{ui.roundError}</p> : null}
          </div>
          <button type="submit" disabled={ui.busy || ui.storageBlocked} className={primary}>
            {ui.action === 'create' ? 'Workflow oluşturuluyor...' : ui.attempt ? 'Workflow Oluşturmayı Tekrar Dene' : 'Workflow Oluştur'}
          </button>
          {ui.attempt ? <p className="text-xs leading-relaxed text-amber-200">Önceki oluşturma yanıtı alınmamış olabilir. Tekrar denemek aynı kimliği ve aynı tur sınırını kullanır; yeni bir deneme oluşturmaz.</p> : null}
        </form> : <div className="space-y-4">
          <div className="space-y-2 rounded-xl border border-slate-700 bg-slate-950/60 p-4">
            <p className="text-xs font-medium text-slate-400">Workflow kimliği</p>
            <code className="block break-all text-sm text-slate-100">{workflowId}</code>
            <p className="text-xs text-slate-400">En fazla {ui.attempt?.rounds} değerlendirme turu</p>
          </div>
          {ui.createdNote ? <p role="status" className="text-sm text-slate-200">{ui.createdNote}</p> : null}
          {ui.attempt?.start === 'accepted' ? <div role="status" aria-live="polite" className="space-y-2 rounded-xl border border-indigo-400/40 bg-indigo-950/40 p-4 text-sm text-indigo-100">
            <p className="font-semibold">Optimizasyon isteği kabul edildi.</p>
            <p>İşlem arka planda sürer. Bu kabul, tamamlandığı veya kalite koşullarını sağladığı anlamına gelmez.</p>
            <a href="#workflow-monitor" className="inline-flex min-h-11 items-center underline underline-offset-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Canlı İlerlemeyi Görüntüle</a>
            <p>Aşağıdaki ilerleme alanı kalıcı durumu okur; ayrıntılı sonuç ekranı sonraki aşamada eklenecek.</p>
            <Link to={ROUTES.promptHistory} className="inline-flex min-h-11 items-center underline underline-offset-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Prompt Geçmişine Git</Link>
          </div> : ui.attempt?.start === 'unknown' && ui.action === 'start' ? <p role="status" className="text-sm text-slate-200">Başlatma yanıtı bekleniyor...</p>
            : ui.attempt?.start === 'unknown' ? <div role="status" aria-live="polite" className="space-y-2 rounded-xl border border-amber-500/40 bg-amber-950/30 p-4 text-sm text-amber-100">
            <p className="font-semibold">Başlatma isteğinin sonucu belirsiz.</p>
            <p>Sunucu isteği kabul etmiş olabilir. Yeniden başlatma gönderilmeyecek; yalnızca kayıtlı durumu kontrol et. Sayfadan ayrılmak sunucudaki işlemi iptal etmez.</p>
          </div> : null}
          <div className="flex flex-wrap gap-3">
            <button type="button" className={secondary} onClick={() => monitor(workflowId!)}>İlerlemeyi Görüntüle</button>
            <button type="button" className={primary} disabled={ui.busy || ui.storageBlocked || ui.startBlocked}
              onClick={() => { void ui.start() }}>{ui.action === 'start' ? 'Başlatma kontrol ediliyor...' : 'Optimizasyonu Başlat'}</button>
            <button type="button" className={secondary} disabled={ui.busy} onClick={() => { void ui.inspect() }}>
              {ui.action === 'read' ? 'Durum okunuyor...' : 'Kayıtlı Durumu Kontrol Et'}
            </button>
          </div>
          {ui.canBeginNew ? <button type="button" className={secondary} disabled={ui.busy || ui.storageBlocked} onClick={ui.configureNew}>Yeni Workflow Yapılandır</button> : null}
        </div>}
        {ui.error && !ui.storageBlocked ? <ErrorAlert {...ui.error} /> : null}
      </div> : null}
    </section>
    <WorkflowMonitor parentId={promptRequestId} defaultWorkflowId={workflowId}
      followDefault={ui.action !== 'start' && (ui.attempt?.start === 'accepted' || ui.attempt?.start === 'unknown')} />
  </>)
}
