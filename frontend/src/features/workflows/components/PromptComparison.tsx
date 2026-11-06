import { useId, useState } from 'react'
import type { WorkflowContentHistory } from '../contracts'
import { promptText } from './resultStyles'

export function PromptComparison({ content, selectedVersionId }: {
  readonly content: WorkflowContentHistory; readonly selectedVersionId?: string | null
}) {
  const id = useId()
  const options = [
    ...(content.source ? [{ id: 'original', label: 'Orijinal prompt', text: content.source.raw_prompt }] : []),
    ...content.versions.map(value => ({ id: value.version.id,
      label: `Sürüm ${value.version.version_number} · Tur ${value.version.evaluation_round ?? 'bilinmiyor'}${value.version.id === selectedVersionId ? ' · Seçilen' : ''}`,
      text: value.rendered_prompt })),
  ]
  const [leftId, setLeft] = useState(options[0]?.id ?? '')
  const [rightId, setRight] = useState(options.find(v => v.id === selectedVersionId)?.id ?? options[1]?.id ?? '')
  const left = options.find(v => v.id === leftId)
  const right = options.find(v => v.id === rightId)
  if (options.length < 2) return <p className="text-sm text-slate-400">Karşılaştırma için iki doğrulanmış metin bulunamadı.</p>
  return <section aria-labelledby={`${id}-heading`} className="min-w-0 space-y-4">
    <h4 id={`${id}-heading`} className="font-semibold text-slate-100">Prompt Karşılaştırması</h4>
    <p className="text-xs text-slate-400">Metinler satır sonları ve girintileri korunarak gösterilir. Biçimlendirme veya yeniden yazım uygulanmaz.</p>
    <div className="grid min-w-0 gap-4 lg:grid-cols-2">{([
      ['Sol prompt', leftId, setLeft, left], ['Sağ prompt', rightId, setRight, right],
    ] as const).map(([label, value, change, option], index) => <div key={label} className="min-w-0 space-y-3">
      <label htmlFor={`${id}-${index}`} className="block text-sm text-slate-300">{label}</label>
      <select id={`${id}-${index}`} value={value} onChange={event => change(event.target.value)} className="min-h-11 w-full min-w-0 rounded-xl border border-slate-700 bg-slate-900 px-3 text-sm text-slate-100 focus-visible:outline-2 focus-visible:outline-indigo-400">
        {options.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
      {option ? <pre aria-label={`${label} metni`} tabIndex={0} className={promptText}>{option.text}</pre> : <p className="text-sm text-amber-200">Seçilen sürüm bu kayıtta bulunamadı.</p>}
    </div>)}</div>
    <p role="status" className="text-sm text-slate-300">{left && right ? left.text === right.text ? 'Seçilen metinler birebir aynı.' : 'Seçilen metinler farklı; iki metni yan yana inceleyebilirsin.' : 'İki mevcut sürüm seç.'}</p>
  </section>
}
