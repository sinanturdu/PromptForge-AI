import type { CriticOutput, QualityStatus } from './contracts'

export const criterionLabels: Readonly<Record<keyof CriticOutput['criteria'], string>> = {
  intent_fidelity: 'Niyete sadakat', clarity: 'Açıklık', completeness: 'Eksiksizlik', structure: 'Yapı',
  context_and_role: 'Bağlam ve rol', constraints_and_scope: 'Kısıtlar ve kapsam', output_format: 'Çıktı biçimi',
  specificity: 'Uygun ayrıntı', consistency: 'Tutarlılık',
}
export const qualityLabels: Readonly<Record<QualityStatus, string>> = {
  qualified: 'Kalite koşulları sağlandı', not_qualified: 'Kalite koşulları sağlanmadı', unassessable: 'Değerlendirilemedi',
}
