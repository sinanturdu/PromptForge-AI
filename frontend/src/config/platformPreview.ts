export type TargetModeId = 'generic' | 'openai' | 'anthropic' | 'google'

export interface EvaluationRoundStage {
  readonly round: number
  readonly title: string
  readonly description: string
}

export interface ArchitecturePillar {
  readonly id: string
  readonly title: string
  readonly description: string
  readonly milestoneTag: string
}

export const EVALUATION_ROUND_STAGES: readonly EvaluationRoundStage[] = [
  {
    round: 1,
    title: 'Tur 1 — İlk Üretim ve Bağımsız Değerlendirme',
    description:
      'Prompt Engineer yapılandırılmış CanonicalPrompt v1 üretir; bağımsız Prompt Critic çıktıyı hedef moda duyarlı rubrikle denetler.',
  },
  {
    round: 2,
    title: 'Tur 2 — Hedefli İlk Revizyon',
    description:
      'Yalnızca 1. tur kalite kapılarını geçemezse tetiklenir; tespit edilen eksikler doğrultusunda en yüksek puanlı sürüm iyileştirilir.',
  },
  {
    round: 3,
    title: 'Tur 3 — Son Revizyon ve Kesin Durdurma Sınırı',
    description:
      'İzin verilen son değerlendirme turudur; iş akışı bu turun ardından deterministik olarak sonlandırılır ve en iyi sürüm seçilir.',
  },
]

export const ARCHITECTURE_PILLARS: readonly ArchitecturePillar[] = [
  {
    id: 'independent-critic',
    title: 'Bağımsız Kalite Denetimi',
    description:
      'Prompt Engineer ve Prompt Critic ayrı talimatlar ve yapılandırılabilir modellerle çalışacak şekilde tasarlanmıştır. Hiçbir ajan kendi çıktısını onaylayamaz.',
    milestoneTag: 'Planlandı · M6–M7',
  },
  {
    id: 'deterministic-bounds',
    title: 'Deterministik 3 Tur Revizyon Sınırı',
    description:
      'Ağırlıklı rubrik puanları LLM yerine uygulama kodunda hesaplanır ve her optimizasyon çalışması en fazla üç değerlendirme turunda tamamlanır.',
    milestoneTag: 'Planlandı · M7–M8',
  },
  {
    id: 'canonical-compiler',
    title: 'CanonicalPrompt ve Hedef Derleyiciler',
    description:
      'Yapılandırılmış prompt içeriği, doğrulanmamış bilgi uydurmadan Universal, ChatGPT / GPT, Claude veya Gemini hedeflerine deterministik olarak derlenir.',
    milestoneTag: 'Planlandı · M6 & M8.5',
  },
]
