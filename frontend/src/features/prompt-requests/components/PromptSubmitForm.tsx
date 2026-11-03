import { useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ROUTES } from '../../../app/routes'
import { ErrorAlert } from '../../../components/common/ErrorAlert'
import { CharacterCounter } from '../../../components/ui/CharacterCounter'
import { ApiClientError } from '../../../lib/api-client'
import { useCreatePromptRequestMutation } from '../hooks'
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
  hasFieldErrors,
  validatePromptFormValues,
  type PromptFormFieldErrors,
} from '../validation'

export function PromptSubmitForm() {
  const navigate = useNavigate()
  const createMutation = useCreatePromptRequestMutation()
  const submissionLockRef = useRef(false)

  const [values, setValues] = useState<PromptFormValues>(DEFAULT_PROMPT_FORM_VALUES)
  const [clientErrors, setClientErrors] = useState<PromptFormFieldErrors>({})
  const [hasAttemptedSubmit, setHasAttemptedSubmit] = useState(false)
  const [submitError, setSubmitError] = useState<ApiClientError | null>(null)

  const isSubmitting = createMutation.isPending

  const backendFieldErrors: PromptFormFieldErrors = {}
  if (submitError) {
    for (const issue of submitError.validationIssues) {
      const key = issue.field as keyof PromptFormFieldErrors
      if (!backendFieldErrors[key]) {
        backendFieldErrors[key] = issue.message
      }
    }
  }

  const rawPromptError = clientErrors.raw_prompt ?? backendFieldErrors.raw_prompt
  const userContextError = clientErrors.user_context ?? backendFieldErrors.user_context
  const outputLanguageError = clientErrors.output_language ?? backendFieldErrors.output_language
  const taskProfileError = clientErrors.task_profile ?? backendFieldErrors.task_profile

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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (submissionLockRef.current || isSubmitting) {
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
      const createdRecord = await createMutation.mutateAsync(payload)
      await navigate(ROUTES.promptRequestDetail(createdRecord.id), {
        state: { justCreated: true },
      })
    } catch (error) {
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
      onSubmit={(event) => {
        void handleSubmit(event)
      }}
      aria-label="Prompt gönderim formu"
      className="space-y-6 rounded-2xl border border-slate-800/90 bg-slate-900/60 p-5 shadow-lg shadow-slate-950/40 sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-4">
        <div>
          <h2 className="text-base font-semibold text-slate-100">
            Prompt İsteği ve Optimizasyon Tercihleri
          </h2>
          <p className="mt-0.5 text-xs text-slate-400">
            Ham isteğinizi ve hedef model tercihlerinizi girerek kalıcı kayıt oluşturun.
          </p>
        </div>
        <span className="rounded-md border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-0.5 text-xs font-medium text-indigo-200">
          POST /api/v1/prompt-requests
        </span>
      </div>

      {submitError ? (
        <ErrorAlert
          title="Prompt isteği kaydedilemedi"
          message={submitError.userMessage}
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
            currentLength={values.rawPrompt.length}
            maxLength={MAX_RAW_PROMPT_LENGTH}
          />
        </div>
        <p id="raw-prompt-hint" className="text-xs text-slate-400">
          Yapay zekâdan gerçekleştirmesini istediğiniz görevi kendi cümlelerinizle yazın. Çok
          satırlı metin yapısı korunur.
        </p>
        <textarea
          id="raw-prompt"
          name="raw_prompt"
          rows={6}
          required
          disabled={isSubmitting}
          value={values.rawPrompt}
          onChange={(event) => updateField('rawPrompt', event.target.value)}
          aria-invalid={Boolean(rawPromptError)}
          aria-describedby={
            rawPromptError
              ? 'raw-prompt-hint raw-prompt-counter raw-prompt-error'
              : 'raw-prompt-hint raw-prompt-counter'
          }
          placeholder="Örn: React ve FastAPI kullanarak ekip bazlı görev atamalarını destekleyen profesyonel bir proje yönetim uygulaması mimarisi tasarla."
          className={`w-full rounded-xl border bg-slate-950/90 p-3.5 text-sm leading-relaxed text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${
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
            currentLength={values.userContext.length}
            maxLength={MAX_USER_CONTEXT_LENGTH}
          />
        </div>
        <p id="user-context-hint" className="text-xs text-slate-400">
          Teknoloji yığını, hedef kitle, kısıtlamalar veya mevcut sistem mimarisi gibi ek arka plan
          bilgilerini ekleyebilirsiniz.
        </p>
        <textarea
          id="user-context"
          name="user_context"
          rows={4}
          disabled={isSubmitting}
          value={values.userContext}
          onChange={(event) => updateField('userContext', event.target.value)}
          aria-invalid={Boolean(userContextError)}
          aria-describedby={
            userContextError
              ? 'user-context-hint user-context-counter user-context-error'
              : 'user-context-hint user-context-counter'
          }
          placeholder="Örn: Uygulama SQLite ile başlayıp ileride PostgreSQL'e taşınacak; arayüz dili Türkçe olmalı."
          className={`w-full rounded-xl border bg-slate-950/90 p-3.5 text-sm leading-relaxed text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${
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
          Bu tercih, ilerleyen sürümlerde prompt derleyicisinin hedefleyeceği formatı belirler;
          seçilen sağlayıcı üzerinde doğrudan model çalıştırmaz.
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
                  checked={isSelected}
                  onChange={() => updateField('targetMode', option.value as TargetMode)}
                  className="sr-only"
                />
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-100">{option.label}</span>
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
                <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
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
            className="w-full rounded-xl border border-slate-800 bg-slate-950/90 px-3.5 py-2.5 text-sm text-slate-100 hover:border-slate-700 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {OUTPUT_LANGUAGE_OPTIONS.map((lang) => (
              <option key={lang.code} value={lang.code} className="bg-slate-950 text-slate-100">
                {lang.label}
              </option>
            ))}
          </select>
          <p className="text-xs text-slate-400">
            Optimize edilecek nihai prompt çıktısı için hedeflenen dil tercihi.
          </p>
          {outputLanguageError ? (
            <p className="text-xs font-medium text-rose-300">{outputLanguageError}</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <label htmlFor="task-profile" className="block text-sm font-medium text-slate-200">
              Görev Profili <span className="text-xs font-normal text-slate-400">(İsteğe Bağlı)</span>
            </label>
            <span className="font-mono text-[11px] text-slate-500">
              {values.taskProfile.trim().length}/{MAX_TASK_PROFILE_LENGTH}
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
            placeholder="Örn: software_development"
            className={`w-full rounded-xl border bg-slate-950/90 px-3.5 py-2.5 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${
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
                  className={`rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-60 ${
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
            <p className="text-xs font-medium text-rose-300">{taskProfileError}</p>
          ) : null}
        </div>
      </div>

      {/* 6, 7 & 8. Detail and Reasoning Preferences */}
      <div className="space-y-5 border-t border-slate-800/80 pt-5">
        {/* Prompt Detail Level */}
        <fieldset disabled={isSubmitting} className="space-y-2">
          <legend className="flex w-full flex-wrap items-baseline justify-between gap-2 text-sm font-medium text-slate-200">
            <span>Prompt Detay Seviyesi</span>
            <span className="font-mono text-xs font-normal text-slate-400">
              {values.promptDetailLevel}
            </span>
          </legend>
          <div className="grid grid-cols-3 gap-2">
            {PROMPT_DETAIL_OPTIONS.map((option) => {
              const isSelected = values.promptDetailLevel === option.value
              return (
                <label
                  key={option.value}
                  className={`cursor-pointer rounded-xl border px-3 py-2.5 text-center text-xs font-medium transition-all focus-within:ring-2 focus-within:ring-indigo-400 ${
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
          <div className="grid grid-cols-3 gap-2">
            {RESPONSE_DETAIL_OPTIONS.map((option) => {
              const isSelected = values.responseDetailPreference === option.value
              return (
                <label
                  key={option.value}
                  className={`cursor-pointer rounded-xl border px-3 py-2.5 text-center text-xs font-medium transition-all focus-within:ring-2 focus-within:ring-indigo-400 ${
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
                  className={`cursor-pointer rounded-xl border px-3 py-2.5 text-center text-xs font-medium transition-all focus-within:ring-2 focus-within:ring-indigo-400 ${
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
            <span className="text-slate-500">
              (Bu ayar üretilecek prompttaki akıl yürütme yönlendirme tercihini belirtir; herhangi
              bir sağlayıcının çıkarım bütçesini doğrudan kontrol etmez.)
            </span>
          </p>
        </fieldset>
      </div>

      {/* Form Submit Footer */}
      <div className="flex flex-col gap-3 border-t border-slate-800/80 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-slate-400">
          Kaydedilen istek veritabanında saklanır ve detay sayfasına yönlendirilirsiniz.
        </p>

        <button
          type="submit"
          disabled={isSubmitting}
          className="inline-flex shrink-0 items-center justify-center rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm shadow-indigo-950/50 transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:bg-indigo-600/50 disabled:text-indigo-100/80"
        >
          {isSubmitting ? 'Kaydediliyor...' : 'Prompt İsteğini Kaydet'}
        </button>
      </div>
    </form>
  )
}
