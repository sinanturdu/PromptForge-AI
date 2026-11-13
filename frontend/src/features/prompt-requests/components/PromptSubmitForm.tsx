import { useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ROUTES } from '../../../app/routes'
import { ErrorAlert } from '../../../components/common/ErrorAlert'
import { CharacterCounter } from '../../../components/ui/CharacterCounter'
import { ApiClientError } from '../../../lib/api-client'
import { useCreatePromptRequestMutation } from '../hooks'
import { applyTextareaEdit, type TextSelection } from '../textareaEdits'
import {
  DEFAULT_PROMPT_FORM_VALUES,
  MAX_RAW_PROMPT_LENGTH,
  MAX_TASK_PROFILE_LENGTH,
  MAX_USER_CONTEXT_LENGTH,
  OUTPUT_LANGUAGE_OPTIONS,
  PROMPT_DETAIL_OPTIONS,
  REASONING_PREFERENCE_OPTIONS,
  RESPONSE_DETAIL_OPTIONS,
  TARGET_AI_FAMILY_OPTIONS,
  TASK_PROFILE_SUGGESTIONS,
  getPromptDetailMeta,
  getReasoningPreferenceMeta,
  getResponseDetailMeta,
  type PromptDetailLevel,
  type PromptFormValues,
  type ReasoningPreference,
  type ResponseDetailPreference,
  type TargetMode,
} from '../types'
import {
  buildCreatePromptRequestPayload,
  characterCount,
  hasFieldErrors,
  validatePromptFormValues,
  type PromptFormFieldErrors,
} from '../validation'

export function PromptSubmitForm({ initialValues, sourceId, canSave, onBack, disabled = false }: {
  readonly initialValues?: PromptFormValues
  readonly sourceId?: string
  readonly canSave?: () => boolean
  readonly onBack?: () => void
  readonly disabled?: boolean
} = {}) {
  const navigate = useNavigate()
  const createMutation = useCreatePromptRequestMutation(sourceId)
  const submissionLockRef = useRef(false)
  const mounted = useRef(false)
  const rawField = useRef<HTMLTextAreaElement>(null)
  const contextField = useRef<HTMLTextAreaElement>(null)
  const textEdit = useRef<{ field: HTMLTextAreaElement; selection: TextSelection } | null>(null)
  useLayoutEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const reviewing = Boolean(initialValues)
  useLayoutEffect(() => {
    if (!reviewing) return
    function remember(event: Event) {
      const field = event.currentTarget as HTMLTextAreaElement
      textEdit.current = { field, selection: { start: field.selectionStart, end: field.selectionEnd } }
    }
    const fields = [rawField.current, contextField.current]
    fields.forEach(field => field?.addEventListener('beforeinput', remember))
    return () => fields.forEach(field => field?.removeEventListener('beforeinput', remember))
  }, [reviewing])

  const [values, setValues] = useState<PromptFormValues>(() => initialValues ?? DEFAULT_PROMPT_FORM_VALUES)
  const [clientErrors, setClientErrors] = useState<PromptFormFieldErrors>({})
  const [hasAttemptedSubmit, setHasAttemptedSubmit] = useState(false)
  const [submitError, setSubmitError] = useState<ApiClientError | null>(null)
  const [saved, setSaved] = useState(false)

  const isSubmitting = createMutation.isPending
  const controlsDisabled = isSubmitting || disabled || saved
  const draftErrors = initialValues ? validatePromptFormValues(values) : undefined

  const backendFieldErrors: PromptFormFieldErrors = {}
  if (submitError) {
    for (const issue of submitError.validationIssues) {
      const key = issue.field as keyof PromptFormFieldErrors
      if (!backendFieldErrors[key]) {
        backendFieldErrors[key] = issue.message
      }
    }
  }

  const errors = draftErrors ?? clientErrors
  const rawPromptError = errors.raw_prompt ?? backendFieldErrors.raw_prompt
  const userContextError = errors.user_context ?? backendFieldErrors.user_context
  const outputLanguageError = errors.output_language ?? backendFieldErrors.output_language
  const taskProfileError = errors.task_profile ?? backendFieldErrors.task_profile

  function updateField<K extends keyof PromptFormValues>(key: K, nextValue: PromptFormValues[K]) {
    const updatedValues: PromptFormValues = {
      ...values,
      [key]: nextValue,
    }
    setValues(updatedValues)

    if (submitError) {
      setSubmitError(null)
    }

    if (hasAttemptedSubmit) {
      setClientErrors(validatePromptFormValues(updatedValues))
    }
  }

  function updateText(key: 'rawPrompt' | 'userContext', field: HTMLTextAreaElement) {
    const edit = textEdit.current
    textEdit.current = null
    updateField(key, reviewing ? applyTextareaEdit(values[key], field.value, field.selectionStart,
      edit?.field === field ? edit.selection : undefined) : field.value)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    const allowed = () => mounted.current && (canSave?.() ?? true)
    if (submissionLockRef.current || controlsDisabled || !allowed()) {
      return
    }

    setHasAttemptedSubmit(true)
    setSubmitError(null)

    const validationErrors = validatePromptFormValues(values)
    setClientErrors(validationErrors)
    if (hasFieldErrors(validationErrors)) {
      return
    }

    submissionLockRef.current = true
    try {
      const payload = buildCreatePromptRequestPayload(values)
      const createdId = await createMutation.save(payload, allowed)
      if (initialValues && mounted.current) setSaved(true)
      if (!allowed()) return
      await navigate(ROUTES.promptRequestDetail(createdId), {
        state: { justCreated: true },
      })
    } catch (error) {
      if (!mounted.current) return
      if (error instanceof ApiClientError) {
        setSubmitError(error)
      } else {
        setSubmitError(
          new ApiClientError({
            status: 500,
            errorCode: 'unexpected_client_error',
            userMessage: 'İstek gönderilirken beklenmeyen bir hata oluştu. Lütfen tekrar deneyin.',
          }),
        )
      }
    } finally {
      submissionLockRef.current = false
    }
  }

  const activePromptDetail = getPromptDetailMeta(values.promptDetailLevel)
  const activeResponseDetail = getResponseDetailMeta(values.responseDetailPreference)
  const activeReasoningPref = getReasoningPreferenceMeta(values.reasoningPreference)

  return (
    <form
      noValidate
      autoComplete={initialValues ? 'off' : undefined}
      onSubmit={(event) => {
        void handleSubmit(event)
      }}
      aria-label="Prompt gönderim formu"
      className="min-w-0 space-y-6 rounded-2xl border border-slate-800/90 bg-slate-900/60 p-5 sm:p-8"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-4">
        <div>
          <h2 className="text-base font-semibold text-slate-100">
            Prompt bilgileri
          </h2>
          <p className="mt-0.5 text-xs text-slate-400">
            Yıldız (*) işaretli alan zorunludur. Diğer tercihleri ihtiyacına göre belirleyebilirsin.
          </p>
        </div>
        {onBack ? <button type="button" disabled={isSubmitting || saved} onClick={() => {
          if (!submissionLockRef.current) onBack()
        }} className="rounded-xl border border-slate-700 px-4 py-2 text-sm text-slate-200 focus-visible:outline-2 focus-visible:outline-indigo-400 disabled:opacity-50">
          Yanıtlara Dön
        </button> : null}
      </div>

      {saved ? <p role="status" className="text-sm text-slate-300">Yeni istek kaydedildi. İsteği Prompt Geçmişinde bulabilirsiniz.</p> : null}
      {submitError ? (
        <ErrorAlert
          title="Prompt isteği kaydedilemedi"
          message={initialValues && (submitError.status === 0 || submitError.status >= 500 ||
            (submitError.status >= 200 && submitError.status < 300))
            ? 'Kaydın sonucu doğrulanamadı. Tekrar denemeden önce Prompt Geçmişini kontrol edin.'
            : submitError.userMessage}
          validationIssues={submitError.validationIssues}
          requestId={submitError.requestId}
        />
      ) : null}

      {/* 1. Raw Prompt */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor="raw-prompt" className="text-sm font-medium text-slate-100">
            Ham Prompt İsteği <span className="text-indigo-400">*</span>
          </label>
          <CharacterCounter
            id="raw-prompt-counter"
            currentLength={characterCount(values.rawPrompt)}
            maxLength={MAX_RAW_PROMPT_LENGTH}
          />
        </div>
        <p id="raw-prompt-hint" className="text-xs text-slate-400">
          Yapay zekâdan gerçekleştirmesini istediğiniz görevi kendi cümlelerinizle yazın. Çok
          satırlı metin yapısı korunur.
        </p>
        <textarea
          ref={rawField}
          id="raw-prompt"
          name="raw_prompt"
          rows={8}
          required
          disabled={isSubmitting}
          value={values.rawPrompt}
          onChange={(event) => updateText('rawPrompt', event.currentTarget)}
          aria-invalid={Boolean(rawPromptError)}
          aria-describedby={
            rawPromptError
              ? 'raw-prompt-hint raw-prompt-counter raw-prompt-error'
              : 'raw-prompt-hint raw-prompt-counter'
          }
          placeholder="Örn: React ve FastAPI kullanarak ekip bazlı görev atamalarını destekleyen profesyonel bir proje yönetim uygulaması mimarisi tasarla."
          className={`w-full rounded-xl border bg-slate-950/90 p-3.5 text-sm leading-relaxed text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${
            rawPromptError
              ? 'border-rose-500/70 focus:border-rose-400 focus:ring-rose-500/40'
              : 'border-slate-800 hover:border-slate-700 focus:border-indigo-500 focus:ring-indigo-500/40'
          }`}
        />
        {rawPromptError ? (
          <p id="raw-prompt-error" className="text-xs font-medium text-rose-300">
            {rawPromptError}
          </p>
        ) : null}
      </div>

      {/* 2. Optional User Context */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor="user-context" className="text-sm font-medium text-slate-200">
            Proje Bağlamı <span className="text-xs font-normal text-slate-400">(İsteğe Bağlı)</span>
          </label>
          <CharacterCounter
            id="user-context-counter"
            currentLength={characterCount(values.userContext)}
            maxLength={MAX_USER_CONTEXT_LENGTH}
          />
        </div>
        <p id="user-context-hint" className="text-xs text-slate-400">
          Teknoloji yığını, hedef kitle, kısıtlamalar veya mevcut sistem mimarisi gibi ek arka plan
          bilgilerini ekleyebilirsiniz.
        </p>
        <textarea
          ref={contextField}
          id="user-context"
          name="user_context"
          rows={4}
          disabled={isSubmitting}
          value={values.userContext}
          onChange={(event) => updateText('userContext', event.currentTarget)}
          aria-invalid={Boolean(userContextError)}
          aria-describedby={
            userContextError
              ? 'user-context-hint user-context-counter user-context-error'
              : 'user-context-hint user-context-counter'
          }
          placeholder="Örn: Uygulama SQLite ile başlayıp ileride PostgreSQL'e taşınacak; arayüz dili Türkçe olmalı."
          className={`w-full rounded-xl border bg-slate-950/90 p-3.5 text-sm leading-relaxed text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${
            userContextError
              ? 'border-rose-500/70 focus:border-rose-400 focus:ring-rose-500/40'
              : 'border-slate-800 hover:border-slate-700 focus:border-indigo-500 focus:ring-indigo-500/40'
          }`}
        />
        {userContextError ? (
          <p id="user-context-error" className="text-xs font-medium text-rose-300">
            {userContextError}
          </p>
        ) : null}
      </div>

      {/* 3. Target AI Family */}
      <fieldset disabled={isSubmitting} className="space-y-2.5">
        <legend className="text-sm font-medium text-slate-100">Hedef Yapay Zekâ Ailesi</legend>
        <p className="text-xs text-slate-400">
          Promptu kullanacağın yapay zekâ ailesini seç. Bu tercih sağlayıcı seçmez ve bir çağrı başlatmaz.
        </p>
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {TARGET_AI_FAMILY_OPTIONS.map((option) => {
            const isSelected = values.targetMode === option.value
            return (
              <label
                key={option.value}
                className={`relative flex cursor-pointer flex-col justify-between rounded-xl border p-3.5 transition-all focus-within:ring-2 focus-within:ring-indigo-400 ${
                  isSelected
                    ? 'border-indigo-500/80 bg-indigo-500/15 shadow-sm ring-1 ring-indigo-500/30'
                    : 'border-slate-800 bg-slate-950/70 hover:border-slate-700 hover:bg-slate-900/80'
                } ${isSubmitting ? 'cursor-not-allowed opacity-60' : ''}`}
              >
                <input
                  type="radio"
                  name="target_mode"
                  value={option.value}
                  aria-labelledby={`target-mode-${option.value}-name`}
                  aria-describedby={`target-mode-${option.value}-description`}
                  checked={isSelected}
                  onChange={() => updateField('targetMode', option.value as TargetMode)}
                  className="sr-only"
                />
                <div className="flex items-center justify-between gap-2">
                  <span id={`target-mode-${option.value}-name`} className="text-sm font-semibold text-slate-100">{option.label}</span>
                  <span
                    className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                      isSelected
                        ? 'border border-indigo-400/40 bg-indigo-500/20 text-indigo-200'
                        : 'border border-slate-800 bg-slate-900 text-slate-400'
                    }`}
                  >
                    {option.badge}
                  </span>
                </div>
                <p id={`target-mode-${option.value}-description`} className="mt-1.5 text-xs leading-relaxed text-slate-400">
                  {option.description}
                </p>
              </label>
            )
          })}
        </div>
      </fieldset>

      {/* 4 & 5. Output Language & Optional Task Profile */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <label htmlFor="output-language" className="block text-sm font-medium text-slate-200">
            Çıktı Dili
          </label>
          <select
            id="output-language"
            name="output_language"
            disabled={isSubmitting}
            value={values.outputLanguage}
            onChange={(event) => updateField('outputLanguage', event.target.value)}
            aria-invalid={Boolean(outputLanguageError)}
            aria-describedby={outputLanguageError ? "output-language-error" : undefined}
            className="w-full rounded-xl border border-slate-800 bg-slate-950/90 px-3.5 py-2.5 text-sm text-slate-100 hover:border-slate-700 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {OUTPUT_LANGUAGE_OPTIONS.map((lang) => (
              <option key={lang.code} value={lang.code} className="bg-slate-950 text-slate-100">
                {lang.label}
              </option>
            ))}
            {initialValues?.outputLanguage && !OUTPUT_LANGUAGE_OPTIONS.some(lang => lang.code === initialValues.outputLanguage)
              ? <option value={initialValues.outputLanguage}>{initialValues.outputLanguage}</option> : null}
            {initialValues ? <option value="">Belirtilmedi</option> : null}
          </select>
          <p className="text-xs text-slate-400">
            Prompt için tercih ettiğin çıktı dili kaydına eklenir.
          </p>
          {outputLanguageError ? (
            <p id="output-language-error" className="text-xs font-medium text-rose-300">{outputLanguageError}</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <label htmlFor="task-profile" className="block text-sm font-medium text-slate-200">
              Görev Profili <span className="text-xs font-normal text-slate-400">(İsteğe Bağlı)</span>
            </label>
            <span className="font-mono text-[11px] text-slate-400">
              {characterCount(values.taskProfile.trim())}/{MAX_TASK_PROFILE_LENGTH}
            </span>
          </div>
          <input
            id="task-profile"
            name="task_profile"
            type="text"
            disabled={isSubmitting}
            value={values.taskProfile}
            onChange={(event) => updateField('taskProfile', event.target.value)}
            aria-invalid={Boolean(taskProfileError)}
            aria-describedby={taskProfileError ? "task-profile-error" : undefined}
            placeholder="Örn: software_development"
            className={`w-full rounded-xl border bg-slate-950/90 px-3.5 py-2.5 text-sm text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${
              taskProfileError
                ? 'border-rose-500/70 focus:border-rose-400 focus:ring-rose-500/40'
                : 'border-slate-800 hover:border-slate-700 focus:border-indigo-500 focus:ring-indigo-500/40'
            }`}
          />
          <div className="flex flex-wrap gap-1.5" aria-label="Önerilen görev profilleri">
            {TASK_PROFILE_SUGGESTIONS.map((suggestion) => {
              const isSelected = values.taskProfile.trim() === suggestion.value
              return (
                <button
                  key={suggestion.value}
                  type="button"
                  disabled={isSubmitting}
                  onClick={() =>
                    updateField('taskProfile', isSelected ? '' : suggestion.value)
                  }
                  className={`min-h-9 rounded-lg border px-3 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-60 ${
                    isSelected
                      ? 'border-indigo-500/50 bg-indigo-500/20 text-indigo-200'
                      : 'border-slate-800 bg-slate-950 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                  }`}
                >
                  {suggestion.label}
                </button>
              )
            })}
          </div>
          {taskProfileError ? (
            <p id="task-profile-error" className="text-xs font-medium text-rose-300">{taskProfileError}</p>
          ) : null}
        </div>
      </div>

      {/* 6, 7 & 8. Detail and Reasoning Preferences */}
      <section aria-labelledby="preferences-heading" className="space-y-5 border-t border-slate-800/80 pt-5">
        <h2 id="preferences-heading" className="text-base font-semibold text-slate-100">Detay tercihleri</h2>
        {/* Prompt Detail Level */}
        <fieldset disabled={isSubmitting} className="space-y-2">
          <legend className="flex w-full flex-wrap items-baseline justify-between gap-2 text-sm font-medium text-slate-200">
            <span>Prompt Detay Seviyesi</span>
            <span className="font-mono text-xs font-normal text-slate-400">
              {values.promptDetailLevel}
            </span>
          </legend>
          <div className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-3">
            {PROMPT_DETAIL_OPTIONS.map((option) => {
              const isSelected = values.promptDetailLevel === option.value
              return (
                <label
                  key={option.value}
                  className={`cursor-pointer flex min-h-11 items-center justify-center rounded-xl border px-3 py-2.5 text-center text-xs font-medium transition-all focus-within:ring-2 focus-within:ring-indigo-400 ${
                    isSelected
                      ? 'border-indigo-500/70 bg-indigo-500/15 text-indigo-100 ring-1 ring-indigo-500/30'
                      : 'border-slate-800 bg-slate-950/70 text-slate-300 hover:border-slate-700 hover:text-slate-100'
                  } ${isSubmitting ? 'cursor-not-allowed opacity-60' : ''}`}
                >
                  <input
                    type="radio"
                    name="prompt_detail_level"
                    value={option.value}
                    checked={isSelected}
                    onChange={() =>
                      updateField('promptDetailLevel', option.value as PromptDetailLevel)
                    }
                    className="sr-only"
                  />
                  {option.label}
                </label>
              )
            })}
          </div>
          <p className="text-xs text-slate-400">{activePromptDetail.description}</p>
        </fieldset>

        {/* Response Detail Preference */}
        <fieldset disabled={isSubmitting} className="space-y-2">
          <legend className="flex w-full flex-wrap items-baseline justify-between gap-2 text-sm font-medium text-slate-200">
            <span>Beklenen Yanıt Detay Tercihi</span>
            <span className="font-mono text-xs font-normal text-slate-400">
              {values.responseDetailPreference}
            </span>
          </legend>
          <div className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-3">
            {RESPONSE_DETAIL_OPTIONS.map((option) => {
              const isSelected = values.responseDetailPreference === option.value
              return (
                <label
                  key={option.value}
                  className={`cursor-pointer flex min-h-11 items-center justify-center rounded-xl border px-3 py-2.5 text-center text-xs font-medium transition-all focus-within:ring-2 focus-within:ring-indigo-400 ${
                    isSelected
                      ? 'border-indigo-500/70 bg-indigo-500/15 text-indigo-100 ring-1 ring-indigo-500/30'
                      : 'border-slate-800 bg-slate-950/70 text-slate-300 hover:border-slate-700 hover:text-slate-100'
                  } ${isSubmitting ? 'cursor-not-allowed opacity-60' : ''}`}
                >
                  <input
                    type="radio"
                    name="response_detail_preference"
                    value={option.value}
                    checked={isSelected}
                    onChange={() =>
                      updateField(
                        'responseDetailPreference',
                        option.value as ResponseDetailPreference,
                      )
                    }
                    className="sr-only"
                  />
                  {option.label}
                </label>
              )
            })}
          </div>
          <p className="text-xs text-slate-400">{activeResponseDetail.description}</p>
        </fieldset>

        {/* Reasoning Preference */}
        <fieldset disabled={isSubmitting} className="space-y-2">
          <legend className="flex w-full flex-wrap items-baseline justify-between gap-2 text-sm font-medium text-slate-200">
            <span>Akıl Yürütme Tercihi</span>
            <span className="font-mono text-xs font-normal text-slate-400">
              {values.reasoningPreference}
            </span>
          </legend>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {REASONING_PREFERENCE_OPTIONS.map((option) => {
              const isSelected = values.reasoningPreference === option.value
              return (
                <label
                  key={option.value}
                  className={`cursor-pointer flex min-h-11 items-center justify-center rounded-xl border px-3 py-2.5 text-center text-xs font-medium transition-all focus-within:ring-2 focus-within:ring-indigo-400 ${
                    isSelected
                      ? 'border-indigo-500/70 bg-indigo-500/15 text-indigo-100 ring-1 ring-indigo-500/30'
                      : 'border-slate-800 bg-slate-950/70 text-slate-300 hover:border-slate-700 hover:text-slate-100'
                  } ${isSubmitting ? 'cursor-not-allowed opacity-60' : ''}`}
                >
                  <input
                    type="radio"
                    name="reasoning_preference"
                    value={option.value}
                    checked={isSelected}
                    onChange={() =>
                      updateField('reasoningPreference', option.value as ReasoningPreference)
                    }
                    className="sr-only"
                  />
                  {option.label}
                </label>
              )
            })}
          </div>
          <p className="text-xs text-slate-400">
            {activeReasoningPref.description}{' '}
            <span className="text-slate-400">
              (Bu ayar üretilecek prompttaki akıl yürütme yönlendirme tercihini belirtir; herhangi
              bir sağlayıcının çıkarım bütçesini doğrudan kontrol etmez.)
            </span>
          </p>
        </fieldset>
      </section>

      {/* Form Submit Footer */}
      <div className="flex flex-col gap-3 border-t border-slate-800/80 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-slate-400">
          Kaydettikten sonra promptunun detaylarını görüntüleyebilirsin.
        </p>

        <button
          type="submit"
          disabled={controlsDisabled || Boolean(draftErrors && hasFieldErrors(draftErrors))}
          className="inline-flex shrink-0 items-center justify-center rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm shadow-indigo-950/50 transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:bg-indigo-600/50 disabled:text-indigo-100/80"
        >
          {isSubmitting ? 'Kaydediliyor...' : 'Prompt İsteğini Kaydet'}
        </button>
      </div>
    </form>
  )
}
