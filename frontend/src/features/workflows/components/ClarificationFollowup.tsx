import { useId, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { PromptSubmitForm } from '../../prompt-requests/components/PromptSubmitForm'
import type { PromptFormValues, PromptRequestRecord } from '../../prompt-requests/types'
import { isBlank } from '../../prompt-requests/validation'
import { buildFollowupDraft, sameQuestions, sameSource } from '../clarificationFollowup'
import { readButton } from './resultStyles'

const answerHint = 'Yanıt alanı boş bırakılamaz.'

export function ClarificationFollowup({ source, questions, checking, canSave, onBindingChange }: {
  readonly source: PromptRequestRecord; readonly questions: readonly string[]; readonly checking: boolean
  readonly canSave: () => boolean
  readonly onBindingChange: () => void
}) {
  const id = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const answerFields = useRef<(HTMLTextAreaElement | null)[]>([])
  const [answers, setAnswers] = useState(() => questions.map(() => ''))
  const [attempted, setAttempted] = useState(false)
  const [draft, setDraft] = useState<PromptFormValues | null>(null)
  // A response with altered questions or source must never inherit the old answers.
  const [basis] = useState(() => ({ source, questions }))
  const stillBound = sameSource(basis.source, source) && sameQuestions(basis.questions, questions)
  useLayoutEffect(() => {
    if (!stillBound) onBindingChange()
  }, [stillBound, onBindingChange])
  useLayoutEffect(() => {
    // Cross-document back/forward can restore an entire page from memory.
    function discard() {
      setAnswers(current => current.map(() => ''))
      setAttempted(false)
      setDraft(null)
    }
    window.addEventListener('pagehide', discard)
    return () => window.removeEventListener('pagehide', discard)
  }, [])
  useLayoutEffect(() => {
    if (draft) heading.current?.focus()
  }, [draft])

  function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (checking || !stillBound || !canSave()) return
    setAttempted(true)
    const firstBlank = answers.findIndex(isBlank)
    if (firstBlank !== -1) {
      answerFields.current[firstBlank]?.focus()
      return
    }
    setDraft(buildFollowupDraft(source, questions, answers))
  }

  if (!stillBound) return null

  return <section aria-labelledby={id} className="min-w-0 space-y-4 rounded-xl border border-slate-700 p-4 sm:p-5">
    <h4 id={id} ref={heading} tabIndex={-1} className="font-semibold text-slate-100">{draft ? 'Yeni Prompt İsteği Taslağı' : 'Ek Bilgi Gerekiyor'}</h4>
    <p className="text-sm text-slate-300">{draft
      ? 'Bu yeni bir prompt isteğidir. Önceki workflow ve geçmiş kayıtlarınız değiştirilmez.'
      : 'Promptunuzu geliştirebilmek için aşağıdaki soruları yanıtlayın. Yanıtlarınızla yeni bir prompt isteği hazırlayabilirsiniz.'}</p>
    <p className="text-xs text-slate-400">Kaydedilmemiş yanıtlar ve taslak, sayfa yenilendiğinde veya bu görünümden ayrıldığınızda silinir.</p>
    {checking ? <p role="status" className="text-xs text-slate-400">Kayıtlar yeniden doğrulanıyor.</p> : null}
    {draft ? <PromptSubmitForm initialValues={draft} sourceId={source.id} canSave={canSave} disabled={checking}
      onBack={() => { setDraft(null); heading.current?.focus() }} /> : <form noValidate autoComplete="off" onSubmit={prepare} className="space-y-4">
      <ol className="list-inside list-decimal space-y-4">
        {questions.map((question, index) => {
          const field = `${id}-answer-${index}`
          const invalid = attempted && isBlank(answers[index] ?? '')
          return <li key={index} className="min-w-0 space-y-2 text-sm text-slate-300">
            <span id={`${field}-question`} className="whitespace-pre-wrap [overflow-wrap:anywhere]">{question}</span>
            <label htmlFor={field} className="block font-medium text-slate-200">Yanıt {index + 1}</label>
            <textarea id={field} ref={node => { answerFields.current[index] = node }}
              value={answers[index]} rows={3} required aria-invalid={invalid}
              aria-describedby={invalid ? `${field}-question ${field}-error` : `${field}-question`}
              onChange={event => {
                const value = event.target.value
                setAnswers(current => current.map((answer, i) => i === index ? value : answer))
              }}
              className="block w-full rounded-xl border border-slate-700 bg-slate-950 p-3 text-slate-100 focus:outline-2 focus:outline-indigo-400 aria-invalid:border-rose-500" />
            {invalid ? <p id={`${field}-error`} role="alert" className="text-xs text-rose-300">{answerHint}</p> : null}
          </li>
        })}
      </ol>
      <button type="submit" className={readButton} disabled={checking}>Yanıtlarımla Yeni İstek Taslağı Hazırla</button>
    </form>}
  </section>
}
