import { useId } from 'react'
import type { WorkflowContentHistory, WorkflowVersionSummary } from '../contracts'
import { sameId } from '../readValidation'
import { RendererIdentity } from './RendererIdentity'
import { promptText } from './resultStyles'

export function PromptComparison({ content, selectedVersionId, choices, onChoose }: {
  readonly content: WorkflowContentHistory; readonly selectedVersionId?: string | null
  readonly choices: readonly [string | null, string | null]
  readonly onChoose: (pane: 0 | 1, id: string) => void
}) {
  const id = useId()
  const options: { id: string; label: string; text: string; version?: WorkflowVersionSummary }[] = [
    ...(content.source ? [{ id: 'original', label: 'Orijinal prompt', text: content.source.raw_prompt }] : []),
    ...content.versions.map(value => ({ id: value.version.id,
      label: `Sürüm ${value.version.version_number} · Tur ${value.version.evaluation_round ?? 'bilinmiyor'}${sameId(value.version.id, selectedVersionId ?? null) ? ' · Seçilen' : ''}`,
      text: value.rendered_prompt, version: value.version })),
  ]
  const left = options.find(v => sameId(v.id, choices[0])) ?? options[0]
  const right = options.find(v => sameId(v.id, choices[1])) ??
    options.find(v => sameId(v.id, selectedVersionId ?? null)) ?? options[1]
  if (options.length < 2) return <p className="text-sm text-slate-400">Karşılaştırma için iki doğrulanmış metin bulunamadı.</p>
  return <section aria-labelledby={`${id}-heading`} className="min-w-0 space-y-4">
    <h4 id={`${id}-heading`} className="font-semibold text-slate-100">Prompt Karşılaştırması</h4>
    <p className="text-xs text-slate-400">Metinler satır sonları ve girintileri korunarak gösterilir. Biçimlendirme veya yeniden yazım uygulanmaz.</p>
    <div className="grid min-w-0 gap-4 lg:grid-cols-2">{([
      ['Sol prompt', 0, left], ['Sağ prompt', 1, right],
    ] as const).map(([label, pane, option]) => <div key={label} className="min-w-0 space-y-3">
      <label htmlFor={`${id}-${pane}`} className="block text-sm text-slate-300">{label}</label>
      <select id={`${id}-${pane}`} value={option?.id ?? ''} onChange={event => onChoose(pane, event.target.value)} className="min-h-11 w-full min-w-0 rounded-xl border border-slate-700 bg-slate-900 px-3 text-sm text-slate-100 focus-visible:outline-2 focus-visible:outline-indigo-400">
        {options.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
      {option?.version ? <RendererIdentity version={option.version} /> : null}
      {option ? <pre aria-label={`${label} metni`} tabIndex={0} className={promptText}>{option.text}</pre> : <p className="text-sm text-amber-200">Seçilen sürüm bu kayıtta bulunamadı.</p>}
    </div>)}</div>
    <p role="status" className="text-sm text-slate-300">{left && right ? left.text === right.text ? 'Seçilen metinler birebir aynı.' : 'Seçilen metinler farklı; iki metni yan yana inceleyebilirsin.' : 'İki mevcut sürüm seç.'}</p>
  </section>
}
