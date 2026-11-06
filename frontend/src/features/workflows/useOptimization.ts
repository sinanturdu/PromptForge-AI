import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { observeWorkflowOutcome } from './availability'
import { isTerminal } from './contracts'
import { WorkflowClientError } from './errors'
import { workflowCreateMutation, workflowMetadataQuery, workflowStartMutation } from './queries'
import { AttemptStorageError, canSubmit, definitiveStartRejection, hasTrustedState,
  newAttempt, readAttempt, writeAttempt, type RoundLimit, type WorkflowAttempt } from './attempts'

interface Failure { readonly title: string; readonly message: string; readonly requestId?: string | null }

function failure(error: unknown, operation: 'create' | 'start' | 'read'): Failure {
  if (error instanceof AttemptStorageError) return { title: 'Deneme kaydı korunamadı', message: error.message }
  const safe = error instanceof WorkflowClientError ? error : new WorkflowClientError('network')
  const availability = observeWorkflowOutcome(operation, safe)
  return {
    title: operation === 'create' ? 'Workflow oluşturulamadı' : operation === 'start' ? 'Başlatma doğrulanamadı' : 'Durum okunamadı',
    message: availability === 'route_unavailable'
      ? 'Workflow API’si kapalı veya yalnızca okumaya açık olabilir. Prompt kaydın korundu.'
      : availability === 'access_denied'
        ? 'İşlem güvenlik politikası tarafından reddedildi. Doğrulanmış yerel uygulamayı kullan; prompt kaydın korundu.'
        : safe.message,
    requestId: safe.requestId,
  }
}

export function useOptimization(parentId: string) {
  const client = useQueryClient()
  const [initial] = useState(() => {
    try { return { attempt: readAttempt(parentId), blocked: false } }
    catch { return { attempt: null, blocked: true } }
  })
  const [attempt, setAttempt] = useState(initial.attempt)
  const current = useRef(attempt)
  const mounted = useRef(true)
  const lock = useRef(false)
  const [expanded, setExpanded] = useState(Boolean(initial.attempt))
  const [rounds, setRounds] = useState(String(initial.attempt?.rounds ?? 3))
  const [roundError, setRoundError] = useState<string | null>(null)
  const [storageBlocked, setStorageBlocked] = useState(initial.blocked)
  const [action, setAction] = useState<'create' | 'start' | 'read' | null>(null)
  const [error, setError] = useState<Failure | null>(null)
  const [createdNote, setCreatedNote] = useState<string | null>(null)
  const [showInspection, setShowInspection] = useState(false)
  const createMutation = useMutation(workflowCreateMutation(client, parentId))
  const startMutation = useMutation(workflowStartMutation(client, attempt?.workflowId ?? ''))
  const inspection = useQuery({ ...workflowMetadataQuery(attempt?.workflowId ?? '', parentId), enabled: false })

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  function commit(next: WorkflowAttempt | null) {
    writeAttempt(parentId, next, current.current)
    current.current = next
    if (mounted.current) setAttempt(next)
  }
  function checkCurrent() {
    if (JSON.stringify(readAttempt(parentId)) !== JSON.stringify(current.current)) throw new AttemptStorageError()
  }
  function report(caught: unknown, operation: 'create' | 'start' | 'read') {
    if (!mounted.current) return
    if (caught instanceof AttemptStorageError) setStorageBlocked(true)
    setError(failure(caught, operation))
  }
  function begin(next: 'create' | 'start' | 'read'): boolean {
    if (lock.current) return false
    lock.current = true
    setAction(next)
    setError(null)
    return true
  }
  function end() {
    lock.current = false
    if (mounted.current) setAction(null)
  }

  async function create() {
    if (storageBlocked || current.current?.workflowId || !begin('create')) return
    try {
      if (!['1', '2', '3'].includes(rounds)) {
        setRoundError('Değerlendirme turu sayısı 1, 2 veya 3 olmalı.')
        return
      }
      setRoundError(null)
      checkCurrent()
      const pending = current.current ?? newAttempt(Number(rounds) as RoundLimit)
      if (current.current === null) commit(pending) // Persist before any POST.
      const result = await createMutation.mutateAsync({ payload: {
        client_idempotency_key: pending.key, max_evaluation_rounds: pending.rounds,
      } })
      if (result.workflow.prompt_request_id !== parentId) throw new WorkflowClientError('malformed_response')
      commit({ ...pending, workflowId: result.workflow.id })
      if (mounted.current) setCreatedNote(result.replayed
        ? 'Önceden oluşturulan workflow bulundu. Aynı kayıt kullanılıyor.'
        : 'Workflow oluşturuldu. Optimizasyon henüz başlatılmadı.')
    } catch (caught) { report(caught, 'create') }
    finally { end() }
  }

  async function inspect() {
    if (!attempt?.workflowId || !begin('read')) return
    try {
      await inspection.refetch({ throwOnError: true })
      if (mounted.current) setShowInspection(true)
    } catch (caught) { report(caught, 'read') }
    finally { end() }
  }

  async function start() {
    const pending = current.current
    if (storageBlocked || !pending?.workflowId || ['unknown', 'accepted'].includes(pending.start) || !begin('start')) return
    setShowInspection(false)
    let submitted = false
    try {
      checkCurrent()
      const snapshot = await inspection.refetch({ throwOnError: true })
      // Leaving the page during preflight must not dispatch a later POST.
      if (!mounted.current) return
      if (!snapshot.data || !canSubmit(snapshot.data)) {
        setShowInspection(true)
        setError({ title: 'Başlatma yapılmadı',
          message: 'Kayıtlı workflow şu anda başlatılmaya uygun değil. Çalışan, sonlanmış veya sonucu belirsiz işlemler yeniden başlatılmaz.' })
        return
      }
      checkCurrent()
      commit({ ...pending, start: 'unknown' }) // Crash/disconnect marker before dispatch.
      setCreatedNote(null)
      submitted = true
      await startMutation.mutateAsync({ payload: { expected_revision: snapshot.data.workflow.state_revision } })
      commit({ ...pending, start: 'accepted' })
    } catch (caught) {
      if (submitted && caught instanceof WorkflowClientError && definitiveStartRejection(caught)) {
        try { commit({ ...pending, start: 'rejected' }) }
        catch (storageError) { report(storageError, 'start'); return }
        if (caught.status === 409 && mounted.current) {
          try { await inspection.refetch({ throwOnError: true }); if (mounted.current) setShowInspection(true) }
          catch { /* Preserve the original sanitized conflict; manual GET remains available. */ }
        }
      }
      report(caught, submitted ? 'start' : 'read')
      if (!submitted && mounted.current && !(caught instanceof AttemptStorageError)) {
        const detail = failure(caught, 'read')
        setError({ ...detail, title: 'Başlatma yapılmadı',
          message: `Durum doğrulanamadı; başlatma isteği gönderilmedi. ${detail.message}` })
      }
    } finally { end() }
  }

  const snapshot = showInspection ? inspection.data : undefined
  const canBeginNew = Boolean(snapshot && hasTrustedState(snapshot) && isTerminal(snapshot.workflow.status))
  function configureNew() {
    if (lock.current || storageBlocked || !canBeginNew) return
    try {
      checkCurrent()
      commit(null)
      setRounds('3')
      setCreatedNote(null)
      setShowInspection(false)
      setError(null)
    } catch (caught) { report(caught, 'create') }
  }

  return { attempt, expanded, setExpanded, rounds, setRounds, roundError, storageBlocked,
    busy: action !== null, action, error, createdNote, snapshot, canBeginNew,
    startBlocked: Boolean(snapshot && !canSubmit(snapshot)) || attempt?.start === 'unknown' || attempt?.start === 'accepted',
    create, start, inspect, configureNew }
}
