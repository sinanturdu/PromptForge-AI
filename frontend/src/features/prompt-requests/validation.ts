import {
  MAX_OUTPUT_LANGUAGE_LENGTH,
  MAX_RAW_PROMPT_LENGTH,
  MAX_TASK_PROFILE_LENGTH,
  MAX_USER_CONTEXT_LENGTH,
  type PromptFormValues,
  type PromptRequestCreatePayload,
} from './types'

export interface PromptFormFieldErrors {
  raw_prompt?: string
  user_context?: string
  output_language?: string
  task_profile?: string
  target_mode?: string
  prompt_detail_level?: string
  response_detail_preference?: string
  reasoning_preference?: string
}

// Match the backend string-field limits for supplementary Unicode characters.
export const characterCount = (value: string): number => [...value].length
// Python str.strip includes U+001C–001F and U+0085, and excludes U+FEFF.
export function isBlank(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0)
    if ((code < 28 || code > 31) && !/\p{White_Space}/u.test(character)) return false
  }
  return true
}

export function validatePromptFormValues(values: PromptFormValues): PromptFormFieldErrors {
  const errors: PromptFormFieldErrors = {}

  if (values.rawPrompt.length === 0) {
    errors.raw_prompt = 'Ham prompt alanı zorunludur.'
  } else if (isBlank(values.rawPrompt)) {
    errors.raw_prompt = 'Ham prompt yalnızca boşluk veya satır sonu karakterlerinden oluşamaz.'
  } else if (characterCount(values.rawPrompt) > MAX_RAW_PROMPT_LENGTH) {
    errors.raw_prompt = `Ham prompt en fazla ${MAX_RAW_PROMPT_LENGTH.toLocaleString('tr-TR')} karakter olabilir.`
  }

  if (characterCount(values.userContext) > MAX_USER_CONTEXT_LENGTH) {
    errors.user_context = `Proje bağlamı en fazla ${MAX_USER_CONTEXT_LENGTH.toLocaleString('tr-TR')} karakter olabilir.`
  }

  if (characterCount(values.outputLanguage.trim()) > MAX_OUTPUT_LANGUAGE_LENGTH) {
    errors.output_language = `Çıktı dili kodu en fazla ${MAX_OUTPUT_LANGUAGE_LENGTH} karakter olabilir.`
  }

  if (characterCount(values.taskProfile.trim()) > MAX_TASK_PROFILE_LENGTH) {
    errors.task_profile = `Görev profili en fazla ${MAX_TASK_PROFILE_LENGTH} karakter olabilir.`
  }

  return errors
}

export function hasFieldErrors(errors: PromptFormFieldErrors): boolean {
  return Object.values(errors).some((msg) => typeof msg === 'string' && msg.length > 0)
}

export function buildCreatePromptRequestPayload(
  values: PromptFormValues,
): PromptRequestCreatePayload {
  const trimmedLanguage = values.outputLanguage.trim()
  const trimmedTaskProfile = values.taskProfile.trim()
  const hasMeaningfulContext = !isBlank(values.userContext)

  return {
    raw_prompt: values.rawPrompt,
    user_context: hasMeaningfulContext ? values.userContext : null,
    output_language: trimmedLanguage.length > 0 ? trimmedLanguage : null,
    target_mode: values.targetMode,
    task_profile: trimmedTaskProfile.length > 0 ? trimmedTaskProfile : null,
    prompt_detail_level: values.promptDetailLevel,
    response_detail_preference: values.responseDetailPreference,
    reasoning_preference: values.reasoningPreference,
  }
}
