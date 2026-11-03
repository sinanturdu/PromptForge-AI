export type TargetMode = 'generic' | 'openai' | 'anthropic' | 'google'
export type PromptDetailLevel = 'low' | 'medium' | 'high'
export type ResponseDetailPreference = 'minimal' | 'balanced' | 'comprehensive'
export type ReasoningPreference = 'auto' | 'low' | 'medium' | 'high'

export const MAX_RAW_PROMPT_LENGTH = 20_000
export const MAX_USER_CONTEXT_LENGTH = 20_000
export const MAX_OUTPUT_LANGUAGE_LENGTH = 32
export const MAX_TASK_PROFILE_LENGTH = 64

export interface PromptRequestCreatePayload {
  readonly raw_prompt: string
  readonly user_context: string | null
  readonly output_language: string | null
  readonly target_mode: TargetMode
  readonly task_profile: string | null
  readonly prompt_detail_level: PromptDetailLevel
  readonly response_detail_preference: ResponseDetailPreference
  readonly reasoning_preference: ReasoningPreference
}

export interface PromptRequestRecord {
  readonly id: string
  readonly raw_prompt: string
  readonly user_context: string | null
  readonly output_language: string | null
  readonly target_mode: TargetMode
  readonly task_profile: string | null
  readonly prompt_detail_level: PromptDetailLevel
  readonly response_detail_preference: ResponseDetailPreference
  readonly reasoning_preference: ReasoningPreference
  readonly created_at: string
}

export interface PromptFormValues {
  rawPrompt: string
  userContext: string
  outputLanguage: string
  taskProfile: string
  targetMode: TargetMode
  promptDetailLevel: PromptDetailLevel
  responseDetailPreference: ResponseDetailPreference
  reasoningPreference: ReasoningPreference
}

export const DEFAULT_PROMPT_FORM_VALUES: PromptFormValues = {
  rawPrompt: '',
  userContext: '',
  outputLanguage: 'tr',
  taskProfile: '',
  targetMode: 'generic',
  promptDetailLevel: 'medium',
  responseDetailPreference: 'balanced',
  reasoningPreference: 'auto',
}

export interface TargetAiFamilyOption {
  readonly value: TargetMode
  readonly label: string
  readonly badge: string
  readonly description: string
}

export interface SelectOptionMeta<TValue extends string> {
  readonly value: TValue
  readonly label: string
  readonly description: string
}

export interface OutputLanguageOption {
  readonly code: string
  readonly label: string
}

export interface TaskProfileSuggestion {
  readonly value: string
  readonly label: string
}

export const TARGET_AI_FAMILY_OPTIONS: readonly TargetAiFamilyOption[] = [
  {
    value: 'generic',
    label: 'Universal',
    badge: 'generic',
    description: 'Modelden bağımsız, tüm üretken yapay zekâ araçlarıyla uyumlu yapılandırılmış format.',
  },
  {
    value: 'openai',
    label: 'ChatGPT / GPT',
    badge: 'openai',
    description: 'Hiyerarşik Markdown başlıkları ve net görev sınırlarıyla GPT ailesine uygun yapı.',
  },
  {
    value: 'anthropic',
    label: 'Claude',
    badge: 'anthropic',
    description: 'Açık bölüm sınırları ve XML benzeri bağlam ayrımıyla Claude modellerine uyumlu yapı.',
  },
  {
    value: 'google',
    label: 'Gemini',
    badge: 'google',
    description: 'Kısıtlamaları ve yapılandırılmış çıktı beklentilerini öne çıkaran Gemini uyumlu düzen.',
  },
]

export const OUTPUT_LANGUAGE_OPTIONS: readonly OutputLanguageOption[] = [
  { code: 'tr', label: 'Türkçe (tr)' },
  { code: 'en', label: 'İngilizce (en)' },
]

export const TASK_PROFILE_SUGGESTIONS: readonly TaskProfileSuggestion[] = [
  { value: 'software_development', label: 'Yazılım Geliştirme' },
  { value: 'architecture_design', label: 'Mimari Tasarım' },
  { value: 'code_review', label: 'Kod İnceleme' },
  { value: 'data_analysis', label: 'Veri Analizi' },
  { value: 'technical_writing', label: 'Teknik Dokümantasyon' },
]

export const PROMPT_DETAIL_OPTIONS: readonly SelectOptionMeta<PromptDetailLevel>[] = [
  {
    value: 'low',
    label: 'Düşük',
    description: 'Temel hedef ve kritik kuralları içeren özlü prompt yapısı.',
  },
  {
    value: 'medium',
    label: 'Orta',
    description: 'Rol, bağlam ve kısıtlamaları dengeli biçimde yapılandırır.',
  },
  {
    value: 'high',
    label: 'Yüksek',
    description: 'Kapsamlı sınır koşulları, varsayımlar ve ayrıntılı çıktı kuralları içerir.',
  },
]

export const RESPONSE_DETAIL_OPTIONS: readonly SelectOptionMeta<ResponseDetailPreference>[] = [
  {
    value: 'minimal',
    label: 'Minimal',
    description: 'Hedef modelden kısa, doğrudan ve özet yanıtlar ister.',
  },
  {
    value: 'balanced',
    label: 'Dengeli',
    description: 'Gereksiz uzatmadan yeterli açıklama ve örnek derinliği hedefler.',
  },
  {
    value: 'comprehensive',
    label: 'Kapsamlı',
    description: 'Derinlemesine analiz, uç durumlar ve ayrıntılı yapılandırılmış yanıt ister.',
  },
]

export const REASONING_PREFERENCE_OPTIONS: readonly SelectOptionMeta<ReasoningPreference>[] = [
  {
    value: 'auto',
    label: 'Otomatik',
    description: 'Görev karmaşıklığına uygun akıl yürütme yönlendirmesini otomatik belirler.',
  },
  {
    value: 'low',
    label: 'Düşük',
    description: 'Doğrudan sonuca odaklanan hızlı yanıt yaklaşımını tercih eder.',
  },
  {
    value: 'medium',
    label: 'Orta',
    description: 'Temel karar adımlarını yapılandırılmış şekilde ele alır.',
  },
  {
    value: 'high',
    label: 'Yüksek',
    description: 'Çok adımlı analiz, doğrulama ve alternatif değerlendirme rehberliği ister.',
  },
]

export function getTargetFamilyMeta(value: TargetMode): TargetAiFamilyOption {
  return (
    TARGET_AI_FAMILY_OPTIONS.find((option) => option.value === value) ?? TARGET_AI_FAMILY_OPTIONS[0]!
  )
}

export function getPromptDetailMeta(
  value: PromptDetailLevel,
): SelectOptionMeta<PromptDetailLevel> {
  return PROMPT_DETAIL_OPTIONS.find((option) => option.value === value) ?? PROMPT_DETAIL_OPTIONS[1]!
}

export function getResponseDetailMeta(
  value: ResponseDetailPreference,
): SelectOptionMeta<ResponseDetailPreference> {
  return (
    RESPONSE_DETAIL_OPTIONS.find((option) => option.value === value) ?? RESPONSE_DETAIL_OPTIONS[1]!
  )
}

export function getReasoningPreferenceMeta(
  value: ReasoningPreference,
): SelectOptionMeta<ReasoningPreference> {
  return (
    REASONING_PREFERENCE_OPTIONS.find((option) => option.value === value) ??
    REASONING_PREFERENCE_OPTIONS[0]!
  )
}

export function formatOutputLanguage(code: string | null): string {
  if (!code) {
    return 'Belirtilmedi'
  }
  const normalized = code.trim().toLowerCase()
  const known = OUTPUT_LANGUAGE_OPTIONS.find((item) => item.code === normalized)
  return known ? known.label : code
}
