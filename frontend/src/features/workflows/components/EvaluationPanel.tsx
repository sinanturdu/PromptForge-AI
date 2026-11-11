import type { CriticOutput, EvaluationSummary } from '../contracts'
import { criterionLabels, qualityLabels } from '../evaluationLabels'
const issueLabels = { intent_shift: 'Niyet değişikliği', missing_requirement: 'Eksik gereksinim',
  invented_requirement: 'Eklenmiş gereksinim', contradiction: 'Çelişki', unsupported_context: 'Dayanaksız bağlam',
  unnecessary_complexity: 'Gereksiz karmaşıklık', insufficient_specificity: 'Yetersiz ayrıntı',
  task_mismatch: 'Görev uyumsuzluğu', format_mismatch: 'Biçim uyumsuzluğu' } as const
const severityLabels = { critical: 'Kritik', major: 'Önemli', minor: 'Küçük' } as const
const evidenceLabels = { raw_prompt: 'Orijinal prompt', user_context: 'Kullanıcı bağlamı', output_language: 'Çıktı dili',
  task_profile: 'Görev profili', prompt_detail_level: 'Prompt ayrıntısı', response_detail_preference: 'Yanıt ayrıntısı',
  reasoning_preference: 'Akıl yürütme tercihi', target_mode: 'Hedef model', rendered_prompt: 'Aday prompt' } as const

export function EvaluationPanel({ evaluation, assessment }: {
  readonly evaluation: EvaluationSummary; readonly assessment: CriticOutput
}) {
  return <section aria-label={`Tur ${evaluation.evaluation_round} Critic değerlendirmesi`} className="min-w-0 space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h4 className="font-semibold text-slate-100">Critic Değerlendirmesi · Tur {evaluation.evaluation_round}</h4>
      <p className="text-sm text-indigo-200">Toplam puan: {evaluation.stored_total_score ?? 'Değerlendirilemedi'}</p>
    </div>
    <p className="text-sm text-slate-300">{evaluation.quality_status ? qualityLabels[evaluation.quality_status] : 'Kalite sonucu bilinmiyor'}. Yürütme durumundan bağımsızdır.</p>
    <p className="text-sm leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-slate-300">{assessment.summary}</p>
    <dl className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {(Object.keys(criterionLabels) as (keyof CriticOutput['criteria'])[]).map(key => {
        const criterion = assessment.criteria[key]
        return <div key={key} className="min-w-0 rounded-xl border border-slate-800 bg-slate-950/70 p-4">
          <dt className="text-sm font-medium text-slate-200">{criterionLabels[key]}</dt>
          <dd className="mt-2 space-y-2">
            <p aria-label={`${criterionLabels[key]} puanı`} className="font-mono text-lg text-indigo-200">{criterion.score === null ? 'Değerlendirilemedi' : `${criterion.score} / 10`}</p>
            <p className="text-xs leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-slate-400">{criterion.rationale}</p>
            {criterion.evidence.length ? <details className="text-xs text-slate-400"><summary className="cursor-pointer rounded py-2 focus-visible:outline-2 focus-visible:outline-indigo-400">Dayanakları Görüntüle</summary>
              {criterion.evidence.map((item, index) => <blockquote key={index} className="my-2 border-l border-slate-600 pl-3 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{evidenceLabels[item.source]}: {item.quote}</blockquote>)}
            </details> : null}
          </dd>
        </div>
      })}
    </dl>
    {evaluation.gates ? <details className="rounded-xl border border-slate-800 p-4 text-sm text-slate-300">
      <summary className="cursor-pointer rounded font-medium focus-visible:outline-2 focus-visible:outline-indigo-400">Kayıtlı kalite koşulları</summary>
      <dl className="mt-3 space-y-2">{([
        ['Tüm ölçütler değerlendirilebilir', evaluation.gates.all_criteria_assessable],
        ['Toplam en az 85', evaluation.gates.total_at_least_85], ['Kritik sorun yok', evaluation.gates.no_critical_issues],
        ['Niyete sadakat en az 7', evaluation.gates.intent_at_least_7],
      ] as const).map(([label, passed]) => <div key={label} className="flex flex-wrap justify-between gap-2"><dt>{label}</dt><dd>{passed === null ? 'Değerlendirilemedi' : passed ? 'Sağlandı' : 'Sağlanmadı'}</dd></div>)}</dl>
      <p className="mt-3 text-xs text-slate-400">Koşul sonuçları backend tarafından sağlanır; bu ekran puan veya karar hesaplamaz.</p>
    </details> : null}
    <div className="space-y-3">
      <h5 className="text-sm font-semibold text-slate-200">Bulgular ve Öneriler</h5>
      {assessment.issues.length ? <ul className="space-y-3">{assessment.issues.map((issue, index) => <li key={index} className="min-w-0 space-y-2 rounded-xl border border-amber-500/25 p-4 text-sm">
        <p className="text-amber-200">{criterionLabels[issue.criterion]} · {issueLabels[issue.code]} · {issue.status === 'uncertain' ? 'Belirsiz yargı; önem derecesi atanmadı' : issue.severity ? severityLabels[issue.severity] : 'Önem derecesi bilinmiyor'}</p>
        <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-slate-300">{issue.description}</p>
        <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-slate-300">Öneri: {issue.recommendation}</p>
        {issue.evidence.map((item, i) => <blockquote key={i} className="border-l border-slate-600 pl-3 text-xs whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-slate-400">{evidenceLabels[item.source]}: {item.quote}</blockquote>)}
      </li>)}</ul> : <p className="text-sm text-slate-400">Bu değerlendirmede kayıtlı bulgu yok.</p>}
    </div>
  </section>
}
